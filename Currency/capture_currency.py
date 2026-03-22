import cv2
import os

folder_name = "Currency"
if not os.path.exists(folder_name):
    os.makedirs(folder_name)

print("="*50)
print("📸 NEW CURRENCY LEARNER 📸")
print("="*50)
name = input("Enter the name of the Currency you are about to scan\n(e.g., '10 Rupees' or '100 Rupees'): ")

if not name.strip():
    print("Invalid name. Exiting...")
    exit()

print(f"\nAwesome! Opening camera to scan '{name}'.")
print("1. Hold the note steadily in front of the camera.")
print("2. Make sure it's well-lit and clear.")
print("3. Press the SPACEBAR to snap the picture and save it!")
print("4. Press 'q' to cancel without saving.")

camera = cv2.VideoCapture(0)

while True:
    ret, frame = camera.read()
    if not ret:
        print("Camera error.")
        break
        
    # Draw instructions on the screen
    display_frame = frame.copy()
    cv2.putText(display_frame, f"Scanning: {name}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
    cv2.putText(display_frame, "Press SPACE to SNAP", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    
    cv2.imshow("Currency Capture", display_frame)
    
    key = cv2.waitKey(1)
    if key == 32: # SPACEBAR
        # Save the full camera frame!
        file_path = os.path.join(folder_name, f"{name}.jpg")
        cv2.imwrite(file_path, frame)
        print(f"\n✅ SUCCESS! Saved image to: {file_path}")
        print("You can now run 'python main.py' and it will instantly recognize this note!")
        break
    elif key == ord('q') or key == 27:
        print("\nCancelled.")
        break

camera.release()
cv2.destroyAllWindows()
