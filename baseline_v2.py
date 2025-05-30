import os
import cv2
import csv
import mediapipe as mp
import numpy as np
from pathlib import Path
from collections import deque
from sklearn.metrics import classification_report

# Define paths (Linux format)
BASE_PATH = Path("./data/Clips")  
ANNOTATIONS_PATH = Path("./data/annotations.csv")  

# Load annotations
if not ANNOTATIONS_PATH.exists():
    raise FileNotFoundError("Annotations file not found!")

annotations = {}
deceptive_videos = []
truthful_videos = []

with open(ANNOTATIONS_PATH, "r") as f:
    reader = csv.DictReader(f)  
    for row in reader:
        video_file = row["id"].strip()
        label = row["class"].strip().lower()
        
        # Store based on label
        if label == "deceptive" and len(deceptive_videos) < 10:
            deceptive_videos.append(video_file)
        elif label == "truthful" and len(truthful_videos) < 10:
            truthful_videos.append(video_file)
        
        annotations[video_file] = label
        if len(deceptive_videos) >= 10 and len(truthful_videos) >= 10:
            break

test_videos = deceptive_videos + truthful_videos
print(f"Evaluating on {len(test_videos)} test videos ({len(deceptive_videos)} deceptive, {len(truthful_videos)} truthful).")

# Initialize MediaPipe Face Mesh
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False, 
    max_num_faces=1, 
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# Feature Extraction Functions
def calculate_ear(landmarks, indices):
    if len(indices) < 6:
        return 0.25  # Default to a normal EAR value if invalid input
    try:
        A = np.linalg.norm(np.array(landmarks[indices[1]]) - np.array(landmarks[indices[5]]))
        B = np.linalg.norm(np.array(landmarks[indices[2]]) - np.array(landmarks[indices[4]]))
        C = np.linalg.norm(np.array(landmarks[indices[0]]) - np.array(landmarks[indices[3]]))
        return (A + B) / (2.0 * C) if C != 0 else 0.3  # Avoid division by zero
    except:
        return 0.25

def calculate_mar(landmarks, indices):
    if len(indices) < 8:
        return 0.25  
    try:
        A = np.linalg.norm(np.array(landmarks[indices[1]]) - np.array(landmarks[indices[7]]))
        B = np.linalg.norm(np.array(landmarks[indices[2]]) - np.array(landmarks[indices[6]]))
        C = np.linalg.norm(np.array(landmarks[indices[3]]) - np.array(landmarks[indices[5]]))
        D = np.linalg.norm(np.array(landmarks[indices[0]]) - np.array(landmarks[indices[4]]))
        return (A + B + C) / (3.0 * D) if D != 0 else 0.3
    except:
        return 0.25

def detect_blinks(ear, prev_ear, threshold=0.2):
    return prev_ear > threshold and ear < threshold  

def calculate_asymmetry(landmarks, left_indices, right_indices):
    try:
        left_avg = np.mean([landmarks[i] for i in left_indices], axis=0)
        right_avg = np.mean([landmarks[i] for i in right_indices], axis=0)
        return np.linalg.norm(left_avg - right_avg)
    except:
        return 0

def calculate_lip_tightness(landmarks, indices):
    try:
        # Upper and lower lip distance
        upper_lip = np.mean([landmarks[indices[0]], landmarks[indices[1]]], axis=0)
        lower_lip = np.mean([landmarks[indices[2]], landmarks[indices[3]]], axis=0)
        return np.linalg.norm(upper_lip - lower_lip)
    except:
        return 0.1

def calculate_micro_expressions(ear_history, mar_history, window=5):
    if len(ear_history) < window or len(mar_history) < window:
        return 0
    
    # Calculate rapid changes in facial metrics
    ear_changes = np.diff(list(ear_history)[-window:])
    mar_changes = np.diff(list(mar_history)[-window:])
    
    # Return a score based on variability
    return np.std(ear_changes) * 50 + np.std(mar_changes) * 50

def calculate_eyebrow_movements(landmarks, eyebrow_indices):
    try:
        # Calculate vertical position of eyebrows relative to eyes
        eyebrow_pos = np.mean([landmarks[i][1] for i in eyebrow_indices])
        eye_pos = np.mean([landmarks[i][1] for i in LEFT_EYE_INDICES + RIGHT_EYE_INDICES])
        return eye_pos - eyebrow_pos  # Positive value means raised eyebrows
    except:
        return 0

# Calculate deception score with normalized values and improved weighting
def calculate_deception_score(features, history):
    # Initialize base score
    deception_score = 0
    
    # Extract current values and history averages
    ear = features['ear']
    mar = features['mar']
    # asymmetry = features['asymmetry']
    blink_rate = features['blinks_per_sec']
    head_movement = features['head_movement']
    micro_expression_score = features['micro_expressions']
    eyebrow_movement = features['eyebrow_movement']
    lip_tightness = features['lip_tightness']
    gaze_shift = features['gaze_shift']
    
    # Calculate averages from history
    avg_ear = np.mean(list(history['ear'])) if history['ear'] else 0.3
    avg_mar = np.mean(list(history['mar'])) if history['mar'] else 0.3
    # avg_asymmetry = np.mean(list(history['asymmetry'])) if history['asymmetry'] else 0.0
    avg_blink_rate = np.mean(list(history['blinks'])) if history['blinks'] else 0.4
    avg_head_movement = np.mean(list(history['head_movement'])) if history['head_movement'] else 0.0
    avg_eyebrow_movement = np.mean(list(history['eyebrow_movement'])) if history['eyebrow_movement'] else 0.0
    avg_lip_tightness = np.mean(list(history['lip_tightness'])) if history['lip_tightness'] else 0.1
    
    # Create normalized deviation scores (how far from baseline)
    ear_deviation = abs((ear - avg_ear) / max(0.01, avg_ear))
    mar_deviation = abs((mar - avg_mar) / max(0.01, avg_mar))
    # asymmetry_deviation = abs((asymmetry - avg_asymmetry) / max(0.01, avg_asymmetry + 0.01))
    blink_deviation = abs((blink_rate - avg_blink_rate) / max(0.01, avg_blink_rate))
    head_movement_deviation = abs((head_movement - avg_head_movement) / max(0.01, avg_head_movement + 0.01))
    eyebrow_deviation = abs((eyebrow_movement - avg_eyebrow_movement) / max(0.01, avg_eyebrow_movement + 0.01))
    lip_deviation = abs((lip_tightness - avg_lip_tightness) / max(0.01, avg_lip_tightness))
    
    # Apply weighted scoring based on research-backed deception cues
    if ear_deviation > 0.25:  # Eye contact changes
        deception_score += ear_deviation * 1.5
    
    if mar_deviation > 0.25:  # Mouth tension
        deception_score += mar_deviation * 1.5
    
    # if asymmetry_deviation > 0.2:  # Facial asymmetry
    #     deception_score += asymmetry_deviation * 1.2
    
    # Blinking - both too little and too much
    if blink_rate < 0.1 or blink_rate > 0.8:  
        deception_score += blink_deviation * 1.0
    
    if head_movement_deviation > 0.5:  # Excessive movement
        deception_score += head_movement_deviation * 0.8
    
    if micro_expression_score > 0.02:  # Micro expressions
        deception_score += micro_expression_score * 50  # Scale appropriately
    
    if eyebrow_deviation > 0.4:  # Eyebrow raises
        deception_score += eyebrow_deviation * 0.5
    
    if lip_deviation > 0.25:  # Lip tightness
        deception_score += lip_deviation * 0.7
    
    # Apply a sigmoid normalization to keep scores in a reasonable range
    normalized_score = 6 * (1 / (1 + np.exp(-0.2 * (deception_score - 20))))    
    return normalized_score

# Indices for facial landmarks
LEFT_EYE_INDICES = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_INDICES = [362, 385, 387, 263, 373, 380]
MOUTH_INDICES = [61, 146, 91, 181, 84, 17, 314, 405]
LEFT_EYEBROW_INDICES = [70, 63, 105, 66, 107]
RIGHT_EYEBROW_INDICES = [336, 296, 334, 293, 300]
LEFT_CHEEK_INDICES = [50, 101, 205, 199]
RIGHT_CHEEK_INDICES = [280, 330, 425, 423]
LIP_INDICES = [0, 17, 61, 291]  # Top center, bottom center, left corner, right corner

# Emotion/Deception relevant connections to visualize
CONNECTIONS_OF_INTEREST = [
    (LEFT_EYE_INDICES[0], LEFT_EYE_INDICES[3]),  # Left eye horizontal
    (RIGHT_EYE_INDICES[0], RIGHT_EYE_INDICES[3]),  # Right eye horizontal
    (MOUTH_INDICES[0], MOUTH_INDICES[4]),  # Mouth horizontal
    (MOUTH_INDICES[2], MOUTH_INDICES[6]),  # Mouth vertical
    (LEFT_EYEBROW_INDICES[0], LEFT_EYEBROW_INDICES[4]),  # Left eyebrow
    (RIGHT_EYEBROW_INDICES[0], RIGHT_EYEBROW_INDICES[4]),  # Right eyebrow
]

def process_video(video_path):
    cap = cv2.VideoCapture(str(video_path))
    
    # Get video stats for overlay
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Rolling History
    window_size = 20  # Increased from 10
    ear_history = deque(maxlen=window_size)
    mar_history = deque(maxlen=window_size)
    # asymmetry_history = deque(maxlen=window_size)
    blink_history = deque(maxlen=window_size)
    head_movement_history = deque(maxlen=window_size)
    eyebrow_movement_history = deque(maxlen=window_size)
    lip_tightness_history = deque(maxlen=window_size)
    deception_score_history = deque(maxlen=10)  # Increased from 5

    prev_ear = 0.3  
    blinks = 0  
    frame_count = 0  
    prev_head_x, prev_head_y = None, None
    gaze_directions = deque(maxlen=window_size)
    speech_rate_indicators = deque(maxlen=window_size)

    # Debug info
    feature_values = {}
    
    # For storing feature values over time (for visualization)
    feature_trends = {
        'ear': deque(maxlen=100),
        'mar': deque(maxlen=100),
        # 'asymmetry': deque(maxlen=100),
        'blinks': deque(maxlen=100),
        'head_movement': deque(maxlen=100),
        'micro_expressions': deque(maxlen=100),
        'eyebrow_movement': deque(maxlen=100),
        'lip_tightness': deque(maxlen=100),
        'deception_score': deque(maxlen=100),
    }
    
    # Calibration phase - first 30 frames to establish baseline
    calibration_frames = min(30, int(fps * 2))  # 2 seconds or 30 frames
    calibration_data = {
        'ear': [],
        'mar': [],
        # 'asymmetry': [],
        'blink_rate': [],
        'head_movement': [],
        'eyebrow_movement': [],
        'lip_tightness': [],
    }
    
    # Process frames
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb_frame)

        # Create a copy for drawing
        display_frame = frame.copy()

        if results.multi_face_landmarks:
            for face_landmarks in results.multi_face_landmarks:
                # Draw the face mesh on the display frame
                mp_drawing.draw_landmarks(
                    image=display_frame,
                    landmark_list=face_landmarks,
                    connections=mp_face_mesh.FACEMESH_CONTOURS,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_drawing_styles.get_default_face_mesh_contours_style()
                )
                
                # Convert normalized coordinates to pixel coordinates
                landmarks_px = [(int(l.x * frame.shape[1]), int(l.y * frame.shape[0])) for l in face_landmarks.landmark]

                # Compute Features
                ear = (calculate_ear(landmarks_px, LEFT_EYE_INDICES) + calculate_ear(landmarks_px, RIGHT_EYE_INDICES)) / 2.0
                mar = calculate_mar(landmarks_px, MOUTH_INDICES)
                # asymmetry = calculate_asymmetry(landmarks_px, LEFT_CHEEK_INDICES, RIGHT_CHEEK_INDICES)
                lip_tightness = calculate_lip_tightness(landmarks_px, LIP_INDICES)
                eyebrow_movement = calculate_eyebrow_movements(landmarks_px, LEFT_EYEBROW_INDICES + RIGHT_EYEBROW_INDICES)

                # Gaze direction (based on eye landmarks)
                left_eye_center = np.mean([landmarks_px[i] for i in LEFT_EYE_INDICES], axis=0)
                right_eye_center = np.mean([landmarks_px[i] for i in RIGHT_EYE_INDICES], axis=0)
                face_center = np.mean([landmarks_px[i] for i in [1, 33, 61, 199, 263, 291]], axis=0)
                
                gaze_vector = np.mean([left_eye_center, right_eye_center], axis=0) - face_center
                gaze_direction = np.arctan2(gaze_vector[1], gaze_vector[0])
                gaze_directions.append(abs(gaze_direction))  # Store absolute deviation from center

                # Blink Detection
                if detect_blinks(ear, prev_ear):
                    blinks += 1  
                prev_ear = ear  

                # Head Movement
                head_x, head_y = landmarks_px[0]  
                if prev_head_x is not None and prev_head_y is not None:
                    head_movement = np.linalg.norm([head_x - prev_head_x, head_y - prev_head_y])
                else:
                    head_movement = 0  
                prev_head_x, prev_head_y = head_x, head_y  

                # Speech rate proxy (mouth movement frequency)
                if len(mar_history) > 2:
                    speech_rate = np.abs(mar - list(mar_history)[-1])
                    speech_rate_indicators.append(speech_rate)
                else:
                    speech_rate_indicators.append(0)

                # Store in Rolling History
                ear_history.append(ear)
                mar_history.append(mar)
                # asymmetry_history.append(asymmetry)
                blink_history.append(blinks / max(1, frame_count / fps))  # Blinks per second
                head_movement_history.append(head_movement)
                eyebrow_movement_history.append(eyebrow_movement)
                lip_tightness_history.append(lip_tightness)

                # Micro-expressions (rapid facial changes)
                micro_expression_score = calculate_micro_expressions(ear_history, mar_history)

                # During calibration phase, collect baseline data
                if frame_count <= calibration_frames:
                    calibration_data['ear'].append(ear)
                    calibration_data['mar'].append(mar)
                    # calibration_data['asymmetry'].append(asymmetry)
                    calibration_data['blink_rate'].append(blinks / max(1, frame_count / fps))
                    calibration_data['head_movement'].append(head_movement)
                    calibration_data['eyebrow_movement'].append(eyebrow_movement)
                    calibration_data['lip_tightness'].append(lip_tightness)
                    
                    # Display calibration status
                    cv2.putText(display_frame, f"Calibrating: {frame_count}/{calibration_frames}", 
                               (30, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    
                else:
                    # Compute Adaptive Thresholds (with safeguards)
                    avg_ear = np.mean(ear_history) if ear_history else 0.3
                    avg_mar = np.mean(mar_history) if mar_history else 0.3
                    # avg_asymmetry = np.mean(asymmetry_history) if asymmetry_history else 0.0
                    avg_blinks = np.mean(blink_history) if blink_history else 0.4
                    avg_head_movement = np.mean(head_movement_history) if head_movement_history else 0.0
                    avg_eyebrow_movement = np.mean(eyebrow_movement_history) if eyebrow_movement_history else 0.0
                    avg_lip_tightness = np.mean(lip_tightness_history) if lip_tightness_history else 0.1
                    avg_gaze_shift = np.mean(gaze_directions) if gaze_directions else 0.0
                    avg_speech_rate = np.mean(speech_rate_indicators) if speech_rate_indicators else 0.0

                    # Store for debugging
                    feature_values = {
                        'ear': ear,
                        'avg_ear': avg_ear,
                        'mar': mar,
                        'avg_mar': avg_mar,
                        # 'asymmetry': asymmetry,
                        # 'avg_asymmetry': avg_asymmetry,
                        'blinks_per_sec': blinks / max(1, frame_count / fps),
                        'head_movement': head_movement,
                        'micro_expressions': micro_expression_score,
                        'eyebrow_movement': eyebrow_movement,
                        'lip_tightness': lip_tightness,
                        'gaze_shift': abs(gaze_direction),
                        'speech_rate': speech_rate_indicators[-1] if speech_rate_indicators else 0
                    }

                    # Use the improved deception scoring function
                    deception_score = calculate_deception_score(feature_values, feature_trends)
                    deception_score_history.append(deception_score)
                    
                    # Store trends for visualization
                    for key in feature_trends:
                        if key in feature_values:
                            feature_trends[key].append(feature_values[key])
                    feature_trends['deception_score'].append(deception_score)

                    # Visualization with detailed information
                    # Draw face mesh connection highlights for emotional cues
                    for connection in CONNECTIONS_OF_INTEREST:
                        pt1 = landmarks_px[connection[0]]
                        pt2 = landmarks_px[connection[1]]
                        # Highlight key facial regions in red
                        cv2.line(display_frame, pt1, pt2, (0, 0, 255), 2)
                    
                    # Information panel
                    y_offset = 30
                    cv2.putText(display_frame, f"Deception Score: {deception_score:.2f}", 
                               (30, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    y_offset += 25
                    
                    # Print some key metrics
                    metrics_to_show = [
                        f"EAR: {ear:.2f} (Avg: {avg_ear:.2f})",
                        f"MAR: {mar:.2f} (Avg: {avg_mar:.2f})",
                        # f"Asymmetry: {asymmetry:.2f}",
                        f"Micro-expr: {micro_expression_score:.4f}"
                    ]
                    
                    for metric in metrics_to_show:
                        cv2.putText(display_frame, metric, 
                                   (30, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                        y_offset += 20
                    
                    # Create a color indicator for the deception level
                    color = (0, 255, 0)  # Default green (truthful)
                    if np.mean(list(deception_score_history)) > 6.5:  # Increased threshold
                        color = (0, 0, 255)  # Red (deceptive)
                    elif np.mean(list(deception_score_history)) > 4.5:  # Increased threshold
                        color = (0, 165, 255)  # Orange (suspicious)
                    
                    # Draw a colored rectangle to indicate deception level
                    cv2.rectangle(display_frame, (frame_width-100, 20), (frame_width-20, 50), color, -1)
                    cv2.putText(display_frame, "TRUTH" if color == (0, 255, 0) else 
                               "SUSPICIOUS" if color == (0, 165, 255) else "DECEPTIVE", 
                               (frame_width-95, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

                    # Debug info every 30 frames
                    if frame_count % 30 == 0:
                        print(f"Frame {frame_count}: Deception Score = {deception_score:.2f}")
                        # print(f"  EAR: {ear:.3f}, MAR: {mar:.3f}, Asymmetry: {asymmetry:.3f}")
                        print(f"  EAR: {ear:.3f}, MAR: {mar:.3f}")
                        print(f"  Micro-expressions: {micro_expression_score:.5f}")
                        print(f"  Eyebrow movement: {eyebrow_movement:.3f}")
                        print(f"  Current avg score: {np.mean(list(deception_score_history)):.2f}")
                        print("-" * 40)

        # If no face detected, show a warning
        else:
            cv2.putText(display_frame, "No face detected", (30, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow("Deception Analysis", display_frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    # Final Prediction based on overall score history
    avg_score = np.mean(list(deception_score_history)) if deception_score_history else 0
    
    # Improved classification threshold with better calibration
    # These thresholds are now higher to reduce false positives
    threshold = 5.5  # Significantly increased from 2.5
    
    print(f"Final average deception score: {avg_score:.2f}")
    return "deceptive" if avg_score >= threshold else "truthful"

# Evaluate model
y_true, y_pred = [], []
results = []  # To store detailed results for each video

for video_name in test_videos:
    video_path = BASE_PATH / ("Deceptive" if video_name in deceptive_videos else "Truthful") / video_name
    if not video_path.exists():
        print(f"Warning: Video file {video_name} not found at {video_path}")
        continue
    
    print(f"\nProcessing video: {video_name}")
    prediction = process_video(video_path)
    actual = annotations[video_name]
    
    y_true.append(actual)
    y_pred.append(prediction)
    
    # Store detailed results
    result = {
        'video': video_name,
        'actual': actual,
        'predicted': prediction,
        'correct': prediction == actual
    }
    results.append(result)
    
    print(f"Result: Actual = {actual.upper()}, Predicted = {prediction.upper()}, {'CORRECT' if prediction == actual else 'INCORRECT'}")

# Add class weights for imbalance
class_weights = {
    'truthful': 1.0, 
    'deceptive': 1.0
}

# Print detailed results table
print("\n=== Detailed Results ===")
print("{:<20} {:<15} {:<15} {:<10}".format("Video", "Actual", "Predicted", "Correct"))
print("-" * 60)
for res in results:
    print("{:<20} {:<15} {:<15} {:<10}".format(
        res['video'], 
        res['actual'].upper(), 
        res['predicted'].upper(), 
        str(res['correct']).upper()))

# Print confusion matrix and classification report
print("\n=== Model Performance Metrics ===")
print(classification_report(y_true, y_pred, target_names=["truthful", "deceptive"]))

# Calculate and print accuracy by class
truthful_correct = sum(1 for res in results if res['actual'] == 'truthful' and res['correct'])
truthful_total = sum(1 for res in results if res['actual'] == 'truthful')
deceptive_correct = sum(1 for res in results if res['actual'] == 'deceptive' and res['correct'])
deceptive_total = sum(1 for res in results if res['actual'] == 'deceptive')

print(f"\nAccuracy by class:")
print(f"Truthful: {truthful_correct}/{truthful_total} ({truthful_correct/truthful_total*100:.1f}%)")
print(f"Deceptive: {deceptive_correct}/{deceptive_total} ({deceptive_correct/deceptive_total*100:.1f}%)")