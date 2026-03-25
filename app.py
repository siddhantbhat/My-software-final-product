from flask import Flask, render_template, jsonify, request
import cv2
import numpy as np
import base64
from ultralytics import YOLO
import requests
import pytesseract
from PIL import Image
import io
from threading import Lock
import os
import serial
import serial.tools.list_ports
import time
import threading
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from twilio.rest import Client
import geocoder
import atexit
import re

# ==================== NEW AI IMPORTS ====================
from deepface import DeepFace
# Suppress deepface/tensorflow logs slightly
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

app = Flask(__name__)
app.secret_key = "visionassist2025"

# ==================== API KEYS & ENDPOINTS ====================
GEMINI_API_KEY = "AIzaSyDl9ZLcFVhC956XjWpGQ74MamMsCxbwalA"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

GROQ_API_KEY = "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"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ==================== N8N WEBHOOKS ====================
N8N_FORM_URL = "https://viren9800.app.n8n.cloud/form/ac088b08-2c41-4f7e-8e5c-30a2db492c76"
N8N_CHAT_WEBHOOK = "https://viren9800.app.n8n.cloud/webhook/201421d5-4df5-49da-98db-4f46f97a3a26/chat"

# ==================== TWILIO SMS CONFIG ====================
TWILIO_ACCOUNT_SID = "your_twilio_sid"
TWILIO_AUTH_TOKEN = "your_twilio_token"
TWILIO_PHONE_NUMBER = "+1234567890"
EMERGENCY_PHONE_NUMBER = "+1987654321"

# ==================== EMAIL CONFIG ====================
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_ADDRESS = "your_email@gmail.com"
EMAIL_PASSWORD = "your_app_password"
EMERGENCY_EMAIL = "emergency_contact@gmail.com"

# ==================== ARDUINO SETUP ====================
arduino_connected = False
arduino = None
front_distance = "0"
steps = "0"
sonar_active = True
serial_lock = Lock()
arduino_port = "COM10"

# Detection mode
detection_mode = "normal"

# Configure Tesseract
try:
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    TESSERACT_AVAILABLE = os.path.exists(r'C:\Program Files\Tesseract-OCR\tesseract.exe')
except:
    TESSERACT_AVAILABLE = False
    print("⚠️ Tesseract not found - using AI fallback")

# Load YOLO
try:
    yolo_model = YOLO("yolov8n.pt")
    print("✅ YOLO model loaded")
except Exception as e:
    print(f"❌ YOLO error: {e}")
    yolo_model = None

# Initialize SIFT for Money Detection
try:
    sift = cv2.SIFT_create()
    bf = cv2.BFMatcher()
except Exception as e:
    print(f"SIFT initialization failed: {e}")

# Store states
latest_objects = []
camera_active = False
voice_paused = False
camera_lock = Lock()
sos_active = False
sos_timer = None
emergency_contacts = []

# Load emergency contacts
def load_emergency_contacts():
    global emergency_contacts
    try:
        if os.path.exists('emergency_contacts.json'):
            with open('emergency_contacts.json', 'r') as f:
                emergency_contacts = json.load(f)
        else:
            emergency_contacts = [
                {"name": "Emergency Services", "number": "911", "email": "", "method": "call"},
                {"name": "Family Member", "number": "", "email": "", "method": "sms"}
            ]
    except Exception as e:
        print(f"Error loading contacts: {e}")
        emergency_contacts = []

load_emergency_contacts()

def init_arduino():
    """Initialize Arduino on COM10"""
    global arduino, arduino_connected, front_distance, steps
    
    try:
        arduino = serial.Serial("COM10", 9600, timeout=1)
        time.sleep(2)
        arduino.reset_input_buffer()
        arduino.reset_output_buffer()
        arduino_connected = True
        print(f"✅ Arduino connected on COM10")
        threading.Thread(target=read_serial, daemon=True).start()
        return True
    except Exception as e:
        print(f"❌ Arduino connection error: {e}")
        arduino_connected = False
        return False

def read_serial():
    """Read data from Arduino"""
    global front_distance, steps, arduino_connected
    
    while sonar_active:
        if arduino and arduino_connected:
            try:
                with serial_lock:
                    if arduino.in_waiting > 0:
                        data = arduino.readline().decode().strip()
                        if data.startswith("DISTANCE:"):
                            try:
                                parts = data.split(',')
                                front_distance = parts[0].split(':')[1]
                                steps = parts[1].split(':')[1]
                            except:
                                pass
            except Exception as e:
                arduino_connected = False
        time.sleep(0.1)

# Initialize Arduino
init_arduino()

# ==================== COMMAND PROCESSOR FOR "MY EYE" ====================
class CommandProcessor:
    """Universal command processor for 'My Eye' wake word"""
    
    def __init__(self):
        self.command_patterns = {
            # Navigation Commands
            "navigate": [
                r"navigate to (.*)",
                r"go to (.*)",
                r"find route to (.*)",
                r"take me to (.*)",
                r"directions to (.*)"
            ],
            
            # Camera Commands
            "camera": [
                r"start camera",
                r"turn on camera",
                r"enable camera",
                r"stop camera",
                r"turn off camera",
                r"disable camera"
            ],
            
            # Object Detection Commands
            "detection": [
                r"what (?:do you see|is in front of me|can you see)",
                r"describe (?:surroundings|environment|what's around)",
                r"scan area",
                r"detect objects"
            ],
            
            # Capture face command
            "capture": [
                r"capture image",
                r"capture face",
                r"take photo",
                r"add face",
                r"save face",
                r"add new face",
                r"register face"
            ],

            # Face/Money Detection Additions
            "recognition": [
                r"recognize face",
                r"who is this",
                r"detect money",
                r"what currency is this",
                r"start face recognition",
                r"start face detection",
                r"start money detection",
                r"face mode",
                r"money mode"
            ],
            
            # Stop Recognition Modes
            "stop_recognition": [
                r"stop face detection",
                r"stop face recognition",
                r"stop money detection",
                r"stop recognition",
                r"stop detection",
                r"normal mode",
                r"object mode"
            ],
            
            # Sensor Commands
            "sensor": [
                r"how far (?:is the obstacle|away)",
                r"what'?s the distance",
                r"distance",
                r"how many steps",
                r"steps needed",
                r"is the path clear"
            ],
            
            # Mode Commands
            "mode": [
                r"(?:switch to|enable|activate) (normal|rapid) mode",
                r"set to (normal|rapid) mode",
                r"change to (normal|rapid) mode"
            ],
            
            # RAG/Question Commands
            "rag": [
                r"who is (.*)",
                r"what is (.*)",
                r"when (?:was|did) (.*)",
                r"where is (.*)",
                r"why (?:is|does) (.*)",
                r"how (?:to|do|does) (.*)",
                r"tell me about (.*)",
                r"explain (.*)",
                r"define (.*)"
            ],
            
            # Document/Image Commands
            "document": [
                r"upload (?:a |an |)document",
                r"read (?:a |an |)document",
                r"upload (?:a |an |)image",
                r"describe (?:a |an |)image",
                r"extract text from image",
                r"analyze image"
            ],
            
            # File Upload with specific filename
            "upload_file": [
                r"upload (?:file |document |image )?(.*?)(?:file|document|image)?$"
            ],
            
            # SOS Commands
            "sos": [
                r"(?:trigger|activate) (?:sos|emergency)",
                r"emergency",
                r"sos",
                r"help me",
                r"call for help"
            ],
            
            # SOS Control
            "sos_control": [
                r"cancel (?:sos|emergency)",
                r"test (?:sos|emergency)"
            ],
            
            # Voice Control
            "voice": [
                r"pause voice",
                r"resume voice",
                r"stop speaking",
                r"interrupt",
                r"quiet",
                r"shut up"
            ],
            
            # Location Commands
            "location": [
                r"where am i",
                r"my location",
                r"current location",
                r"share location"
            ],
            
            # Help Commands
            "help": [
                r"help",
                r"what can you do",
                r"commands",
                r"available commands"
            ],
            
            # General Knowledge (fallback to RAG)
            "general": [
                r"(.+)"
            ]
        }
    
    def process_command(self, command_text):
        """Process any command and return the action and parameters"""
        command_text = command_text.lower().strip()
        
        # Remove wake word if present
        wake_words = ["my eye", "my i", "mai eye"]
        for wake_word in wake_words:
            if command_text.startswith(wake_word):
                command_text = command_text.replace(wake_word, "", 1).strip()
                break
            elif wake_word in command_text:
                command_text = command_text.split(wake_word, 1)[1].strip()
                break
                
        # Strip leading punctuation that might be left
        command_text = command_text.lstrip(',.?!;:- ')
        
        # Check each command category
        for category, patterns in self.command_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, command_text, re.IGNORECASE)
                if match:
                    # Extract parameters (the captured group)
                    params = match.group(1) if match.groups() else None
                    return {
                        "category": category,
                        "action": pattern,
                        "params": params,
                        "full_command": command_text
                    }
        
        # Default to general question
        return {
            "category": "general",
            "action": "question",
            "params": command_text,
            "full_command": command_text
        }

# Initialize command processor
command_processor = CommandProcessor()

# ==================== AI ASSISTANT FUNCTIONS ====================
def ask_rag_assistant(question, context=""):
    """Send question to RAG webhook"""
    try:
        payload = {
            "message": question,
            "context": context,
            "timestamp": time.time()
        }
        
        response = requests.post(N8N_CHAT_WEBHOOK, json=payload, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                return data.get('response', str(data))
            elif isinstance(data, str):
                return data
            else:
                return str(data)
        else:
            return f"I couldn't process that request. Please try again."
            
    except Exception as e:
        print(f"RAG error: {e}")
        return f"I'm having trouble connecting. Please check your internet connection."

def ask_groq(prompt, system_message="You are a helpful assistant."):
    """Ask Groq API"""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 300
    }
    
    try:
        response = requests.post(GROQ_URL, json=data, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return None
    except Exception as e:
        print(f"Groq exception: {e}")
        return None

# ==================== MODIFIED FUNCTION: SIMPLE OBJECT DESCRIPTION ====================
def describe_objects_simple(objects):
    """Create a simple description of objects in 'I can see X' format"""
    if not objects:
        return "I don't see any objects in front of you."
    
    # Remove duplicates while preserving order
    unique_objects = []
    for obj in objects:
        if obj not in unique_objects:
            unique_objects.append(obj)
    
    if len(unique_objects) == 1:
        return f"I can see {unique_objects[0]}"
    elif len(unique_objects) == 2:
        return f"I can see {unique_objects[0]} and {unique_objects[1]}"
    else:
        last_object = unique_objects[-1]
        other_objects = unique_objects[:-1]
        objects_text = ", ".join(other_objects)
        return f"I can see {objects_text}, and {last_object}"

def detect_objects(frame):
    """YOLO detection"""
    if yolo_model is None:
        return []
    
    try:
        conf_threshold = 0.15 if detection_mode == "rapid" else 0.25
        results = yolo_model(frame, conf=conf_threshold, verbose=False)
        detected = []
        
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                label = yolo_model.names[cls_id]
                detected.append(label)
        
        return list(dict.fromkeys(detected))
    except:
        return []

def describe_image_with_groq(image_base64):
    """Image description using Groq"""
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Describe this image in detail. What do you see? Be helpful for someone who is visually impaired."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 300
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        result = response.json()
        
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        else:
            return "I couldn't generate a description for this image."
            
    except Exception as e:
        print(f"Image description error: {e}")
        return "Error describing image."

def extract_text_with_ai(image):
    """Text extraction using AI"""
    try:
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_byte_arr = img_byte_arr.getvalue()
        image_base64 = base64.b64encode(img_byte_arr).decode("utf-8")
        
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Please read and extract any text you can see in this image. If there is text, write it exactly as you see it. If there is no text, just say 'No text found in this image'."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 500
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        result = response.json()
        
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        else:
            return None
    except Exception as e:
        return None

# ==================== SOS FUNCTIONS ====================
def send_sms_alert(location, maps_link):
    try:
        if TWILIO_ACCOUNT_SID and TWILIO_ACCOUNT_SID != "your_twilio_sid":
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            message = f"🚨 SOS EMERGENCY! The user needs help!\n📍 Location: {location}\n🗺️ Maps: {maps_link}"
            
            for contact in emergency_contacts:
                if contact.get('number') and contact['method'] in ['sms', 'both']:
                    client.messages.create(
                        body=message,
                        from_=TWILIO_PHONE_NUMBER,
                        to=contact['number']
                    )
        return True
    except Exception as e:
        return False

def send_email_alert(location, maps_link):
    try:
        if EMAIL_ADDRESS and EMAIL_ADDRESS != "your_email@gmail.com":
            msg = MIMEMultipart()
            msg['From'] = EMAIL_ADDRESS
            msg['To'] = EMERGENCY_EMAIL
            msg['Subject'] = "🚨 SOS EMERGENCY ALERT - VisionAssist User Needs Help!"
            
            body = f"""
            <h2>🚨 SOS EMERGENCY ALERT</h2>
            <p><strong>The VisionAssist user has triggered an emergency SOS!</strong></p>
            <h3>📍 Location Information:</h3>
            <p>Coordinates: {location}</p>
            <p>Google Maps: <a href="{maps_link}">{maps_link}</a></p>
            <h3>⏰ Time:</h3>
            <p>{time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            """
            
            msg.attach(MIMEText(body, 'html'))
            
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
            server.quit()
            
            return True
    except Exception as e:
        return False

# ==================== FLASK ROUTES ====================

@app.route('/')
def home():
    return render_template('index.html')

# ==================== FACE CAPTURE ENDPOINT ====================
@app.route('/api/capture-face', methods=['POST'])
def capture_face_api():
    """Save a captured face frame to known_faces/<name>.jpg"""
    try:
        data = request.get_json()
        image_data = data.get('image_data', '')
        name = data.get('name', '').strip()

        if not name:
            return jsonify({"status": "error", "message": "No name provided."}), 400

        if ',' in image_data:
            image_data = image_data.split(',')[1]

        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({"status": "error", "message": "Invalid image."}), 400

        # Face detection check (lightweight Haar cascade)
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
        if len(faces) == 0:
            return jsonify({
                "status": "no_face",
                "message": "No person detected in the frame. Please position yourself directly in front of the camera and try again."
            }), 200

        if not os.path.exists("known_faces"):
            os.makedirs("known_faces")

        # Sanitize name
        safe_name = name.lower().replace(' ', '_')
        save_path = os.path.join("known_faces", f"{safe_name}.jpg")
        cv2.imwrite(save_path, frame)

        # Clear DeepFace cache so it rebuilds with new face
        for pkl_file in os.listdir("known_faces"):
            if pkl_file.endswith(".pkl"):
                try:
                    os.remove(os.path.join("known_faces", pkl_file))
                except:
                    pass

        return jsonify({
            "status": "success",
            "message": f"Face saved for {name}.",
            "path": save_path
        })
    except Exception as e:
        print(f"Capture face error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== NEW: RECOGNITION AND DETECTION ENDPOINTS ====================
@app.route('/api/recognize-face', methods=['POST'])
def recognize_face():
    try:
        data = request.get_json()
        image_data = data.get('image_data', '')
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
            
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if not os.path.exists("known_faces"):
            os.makedirs("known_faces")
            return jsonify({
                "status": "success",
                "audioDescription": "Known faces directory created. Please add face images."
            })
            
        dfs = DeepFace.find(img_path=frame, db_path="known_faces", model_name="Facenet", enforce_detection=False, silent=True)
        
        recognized_name = "Unknown person"
        recognized_faces = []
        if len(dfs) > 0 and not dfs[0].empty:
            match_path = dfs[0].iloc[0]['identity']
            distance = dfs[0].iloc[0].get('distance', 0.0)
            filename = os.path.basename(match_path)
            recognized_name = os.path.splitext(filename)[0].replace("_", " ").title()
            recognized_name = ''.join([i for i in recognized_name if not i.isdigit()]).strip()
            description = f"I recognize {recognized_name}."
            recognized_faces.append({"name": recognized_name, "distance": round(distance, 2)})
        else:
            description = "I see a person, but I don't recognize them."
            
        return jsonify({
            "status": "success",
            "audioDescription": description,
            "recognizedFaces": recognized_faces
        })
    except Exception as e:
        print(f"Face recognition error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/detect-money', methods=['POST'])
def detect_money_api():
    try:
        data = request.get_json()
        image_data = data.get('image_data', '')
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
            
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if not os.path.exists("Currency"):
            return jsonify({"status": "success", "audioDescription": "Currency directory not found."})
            
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        kp_frame, des_frame = sift.detectAndCompute(gray_frame, None)
        
        best_match_name = "Unknown currency"
        max_good_matches = 0
        
        if des_frame is not None:
            for filename in os.listdir("Currency"):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    ref_path = os.path.join("Currency", filename)
                    ref_img = cv2.imread(ref_path, cv2.IMREAD_GRAYSCALE)
                    if ref_img is None:
                        continue
                        
                    kp_ref, des_ref = sift.detectAndCompute(ref_img, None)
                    if des_ref is None:
                        continue
                        
                    matches = bf.knnMatch(des_ref, des_frame, k=2)
                    good_matches = [m for m, n in matches if m.distance < 0.70 * n.distance]
                                
                    if len(good_matches) > max_good_matches and len(good_matches) > 35:
                        max_good_matches = len(good_matches)
                        name = os.path.splitext(filename)[0].split('_')[0]
                        best_match_name = f"{name} Rupees" if name.isdigit() else name
                            
        if max_good_matches > 35:
            description = f"I detect a {best_match_name} note."
            currency_detected = best_match_name
        else:
            description = "I don't clearly see any recognized currency."
            currency_detected = None
            
        return jsonify({
            "status": "success",
            "audioDescription": description,
            "currency": currency_detected
        })
    except Exception as e:
        print(f"Money detection error: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# ==================== VOICE COMMAND ENDPOINT ====================
@app.route('/api/process-command', methods=['POST'])
def process_command():
    """Universal endpoint for processing any voice command"""
    try:
        data = request.get_json()
        command_text = data.get('command', '')
        
        if not command_text:
            return jsonify({"error": "No command provided"}), 400
        
        # Process the command using our command processor
        parsed = command_processor.process_command(command_text)
        category = parsed["category"]
        params = parsed["params"]
        
        response = {
            "status": "success",
            "category": category,
            "original_command": command_text
        }
        
        # ========== HANDLE DIFFERENT COMMAND CATEGORIES ==========
        
        # NAVIGATION COMMANDS
        if category == "navigate" and params:
            response["action"] = "navigate"
            response["destination"] = params
            response["message"] = f"Finding route to {params}"
            response["requires_action"] = True
        
        # CAMERA COMMANDS
        elif category == "camera":
            if "start" in command_text or "turn on" in command_text:
                response["action"] = "start_camera"
                response["message"] = "Starting camera"
            elif "stop" in command_text or "turn off" in command_text:
                response["action"] = "stop_camera"
                response["message"] = "Stopping camera"
        
        # OBJECT DETECTION COMMANDS
        elif category == "detection":
            response["action"] = "describe_scene"
            response["message"] = "Analyzing what's in front of you"

        # CAPTURE FACE COMMAND
        elif category == "capture":
            response["action"] = "start_capture"
            response["message"] = "Ready to capture. Please look at the camera."

        # RECOGNITION COMMANDS (FACE / MONEY)
        elif category == "recognition":
            if "face" in command_text or "who" in command_text:
                response["action"] = "set_mode"
                response["mode"] = "face"
                response["message"] = "Switching to Face Detection Mode."
            elif "money" in command_text or "currency" in command_text:
                response["action"] = "set_mode"
                response["mode"] = "money"
                response["message"] = "Switching to Money Detection Mode."
                
        # STOP RECOGNITION COMMANDS
        elif category == "stop_recognition":
            response["action"] = "set_mode"
            response["mode"] = "normal"
            response["message"] = "Switched back to Normal Object Detection Mode."

        # SENSOR COMMANDS
        elif category == "sensor":
            if "how far" in command_text or "distance" in command_text:
                response["action"] = "get_distance"
                response["message"] = "Checking obstacle distance"
            elif "how many steps" in command_text or "steps needed" in command_text:
                response["action"] = "get_steps"
                response["message"] = "Calculating steps needed"
            elif "path clear" in command_text:
                response["action"] = "check_path"
                response["message"] = "Checking if path is clear"
        
        # MODE COMMANDS
        elif category == "mode" and params:
            mode = params.lower()
            if mode in ["normal", "rapid"]:
                response["action"] = "set_mode"
                response["mode"] = mode
                response["message"] = f"Switching to {mode} mode"
        
        # RAG/QUESTION COMMANDS
        elif category == "rag" and params:
            # Get answer from RAG assistant
            answer = ask_rag_assistant(params)
            response["action"] = "answer_question"
            response["answer"] = answer
            response["message"] = answer
        
        # DOCUMENT/IMAGE COMMANDS
        elif category == "document":
            if "upload" in command_text and ("document" in command_text or "file" in command_text):
                response["action"] = "upload_document"
                response["message"] = "Opening document upload"
            elif "upload" in command_text and ("image" in command_text or "picture" in command_text):
                response["action"] = "upload_image"
                response["message"] = "Opening image upload"
            elif "describe" in command_text and ("image" in command_text or "picture" in command_text):
                response["action"] = "describe_image"
                response["message"] = "Ready to describe image"
            elif "read" in command_text or "extract" in command_text:
                response["action"] = "extract_text"
                response["message"] = "Ready to extract text from image"
        
        # FILE UPLOAD WITH SPECIFIC NAME
        elif category == "upload_file" and params:
            filename = params.strip()
            response["action"] = "upload_specific_file"
            response["filename"] = filename
            response["message"] = f"Looking for file: {filename}"
        
        # SOS COMMANDS
        elif category == "sos":
            response["action"] = "trigger_sos"
            response["message"] = "EMERGENCY! Activating SOS"
            response["emergency"] = True
        
        # SOS CONTROL COMMANDS
        elif category == "sos_control":
            if "cancel" in command_text:
                response["action"] = "cancel_sos"
                response["message"] = "Cancelling SOS"
            elif "test" in command_text:
                response["action"] = "test_sos"
                response["message"] = "Testing SOS system"
        
        # VOICE CONTROL COMMANDS
        elif category == "voice":
            if "pause" in command_text:
                response["action"] = "pause_voice"
                response["message"] = "Voice paused"
            elif "resume" in command_text:
                response["action"] = "resume_voice"
                response["message"] = "Voice resumed"
            elif any(w in command_text for w in ["stop speaking", "interrupt", "quiet", "shut"]):
                response["action"] = "stop_speaking"
                response["message"] = "" # Don't speak any confirmation
        
        # LOCATION COMMANDS
        elif category == "location":
            if "where am i" in command_text or "my location" in command_text:
                response["action"] = "get_location"
                response["message"] = "Getting your current location"
            elif "share" in command_text:
                response["action"] = "share_location"
                response["message"] = "Sharing your location"
        
        # HELP COMMANDS
        elif category == "help":
            help_text = """
            I can help you with:
            • Navigation: "navigate to [place]"
            • Camera: "start/stop camera"
            • Recognition: "recognize face" or "detect money"
            • Detection: "what do you see?"
            • Distance: "how far?" or "how many steps?"
            • Modes: "normal mode" or "rapid mode"
            • Questions: "what is...", "who is..."
            • Documents: "upload document" or "describe image"
            • Emergency: "emergency" or "SOS"
            • Location: "where am I?"
            • Voice: "pause/resume voice"
            """
            response["action"] = "show_help"
            response["help_text"] = help_text
            response["message"] = help_text
        
        # GENERAL QUESTIONS (fallback)
        elif category == "general" and params:
            answer = ask_rag_assistant(params)
            response["action"] = "general_question"
            response["answer"] = answer
            response["message"] = answer
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== MODIFIED ENDPOINT: Now uses simple description ====================
@app.route('/api/analyze-frame', methods=['POST'])
def analyze_frame():
    global latest_objects
    
    try:
        data = request.get_json()
        image_data = data.get('image_data', '')
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        detected_objects = detect_objects(frame)
        # Use simple description instead of Gemini
        description = describe_objects_simple(detected_objects)
        
        with camera_lock:
            latest_objects = detected_objects
        
        return jsonify({
            "status": "success",
            "detectedObjects": detected_objects,
            "audioDescription": description  # Now returns "I can see person" etc.
        })
        
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/sonar-data', methods=['GET'])
def get_sonar_data():
    return jsonify({
        "connected": arduino_connected,
        "port": arduino_port,
        "distance": front_distance,
        "steps": steps,
    })

@app.route('/api/detection-mode', methods=['GET', 'POST'])
def handle_detection_mode():
    global detection_mode
    
    if request.method == 'GET':
        return jsonify({"mode": detection_mode})
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            new_mode = data.get('mode', 'normal')
            
            if new_mode in ['normal', 'rapid']:
                detection_mode = new_mode
                return jsonify({"status": "success", "mode": detection_mode})
            else:
                return jsonify({"error": "Invalid mode"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/arduino/status', methods=['GET'])
def arduino_status():
    return jsonify({
        "connected": arduino_connected,
        "port": arduino_port,
        "distance": front_distance,
        "steps": steps
    })

@app.route('/api/arduino/reconnect', methods=['POST'])
def reconnect_arduino():
    global arduino, arduino_connected
    
    if arduino and arduino.is_open:
        arduino.close()
    
    success = init_arduino()
    
    return jsonify({
        "success": success,
        "connected": arduino_connected,
        "port": arduino_port,
        "distance": front_distance,
        "steps": steps
    })

@app.route('/api/extract-text', methods=['POST'])
def extract_text():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes))
        
        extracted_text = None
        method_used = "none"
        
        if TESSERACT_AVAILABLE:
            try:
                extracted_text = pytesseract.image_to_string(image)
                if extracted_text and extracted_text.strip():
                    extracted_text = extracted_text.strip()
                    method_used = "tesseract"
                else:
                    extracted_text = None
            except:
                extracted_text = None
        
        if not extracted_text:
            extracted_text = extract_text_with_ai(image)
            if extracted_text and extracted_text.strip():
                method_used = "ai"
        
        if extracted_text and extracted_text.strip():
            return jsonify({
                "status": "success",
                "text": extracted_text.strip(),
                "method": method_used
            })
        else:
            return jsonify({
                "status": "success",
                "text": "No text could be extracted from this image.",
                "method": "none"
            })
        
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/describe-image', methods=['POST'])
def describe_image():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        image_bytes = file.read()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        
        description = describe_image_with_groq(image_base64)
        
        return jsonify({
            "status": "success",
            "description": description
        })
        
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/explain-text', methods=['POST'])
def explain_text():
    try:
        data = request.get_json()
        text = data.get('text', '')
        prompt_type = data.get('prompt', 'simple')
        
        if not text or "No text could be extracted" in text:
            return jsonify({
                "status": "success",
                "explanation": "No text was extracted from the image."
            })
        
        if prompt_type == 'simple':
            system_msg = "You are helping a blind person understand text from documents. Use very simple, clear language."
            user_prompt = f"Explain this text in simple words for a blind person:\n\n{text}"
        else:
            system_msg = "You are helping a blind person by summarizing text. Be brief and clear."
            user_prompt = f"Give a brief summary of this text:\n\n{text}"
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 300
        }
        
        response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            result = response.json()
            explanation = result['choices'][0]['message']['content']
            return jsonify({"status": "success", "explanation": explanation})
        else:
            return jsonify({"status": "success", "explanation": f"Here's the text: {text[:200]}..."})
        
    except Exception as e:
        return jsonify({"status": "success", "explanation": "I couldn't explain the text. Please try again."})

@app.route('/api/assistant-query', methods=['POST'])
def assistant_query():
    """RAG assistant endpoint"""
    try:
        data = request.get_json()
        question = data.get('question', '')
        context = data.get('context', '')
        
        if not question:
            return jsonify({"error": "No question provided"}), 400
        
        # Emergency detection
        if 'sos' in question.lower() or 'emergency' in question.lower() or 'help me' in question.lower():
            return jsonify({
                "status": "success",
                "response": "I've detected an emergency request. Triggering SOS system.",
                "trigger_sos": True
            })
        
        # Send to RAG assistant
        response = ask_rag_assistant(question, context)
        
        return jsonify({
            "status": "success",
            "response": response,
            "trigger_sos": False
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/voice-destination', methods=['POST'])
def voice_destination():
    """Correct voice destination using AI"""
    try:
        data = request.get_json()
        text = data.get('text', '')
        alternatives = data.get('alternatives', [])
        
        prompt = f"""The user said this destination: "{text}"
        
        Alternative interpretations: {', '.join(alternatives) if alternatives else 'None'}
        
        Please correct this to a proper location/destination name. If it's a place name, fix any spelling errors.
        Return ONLY the corrected destination name, nothing else."""
        
        corrected = ask_groq(prompt, "You are a location name corrector. Fix spelling and return proper place names.")
        
        if corrected and len(corrected) < 100:
            return jsonify({
                "status": "success",
                "corrected": corrected.strip(),
                "original": text
            })
        else:
            return jsonify({
                "status": "success",
                "corrected": text,
                "original": text
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/rag-upload', methods=['POST'])
def rag_upload():
    """Upload document to RAG system"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        files = {
            "file": (file.filename, file.stream, file.mimetype)
        }
        
        response = requests.post(N8N_FORM_URL, files=files, timeout=30)
        
        if response.status_code == 200:
            return jsonify({
                "status": "success",
                "message": "Document uploaded to RAG knowledge base"
            })
        else:
            return jsonify({
                "status": "error",
                "message": f"Upload failed"
            }), response.status_code
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sos/trigger', methods=['POST'])
def trigger_sos():
    global sos_active, sos_timer
    
    try:
        data = request.get_json()
        location = data.get('location', 'Unknown')
        lat = data.get('lat')
        lon = data.get('lon')
        
        sos_active = True
        
        if lat and lon:
            maps_link = f"https://www.google.com/maps?q={lat},{lon}"
        else:
            maps_link = "Location not available"
        
        sms_sent = send_sms_alert(location, maps_link)
        email_sent = send_email_alert(location, maps_link)
        
        sos_timer = threading.Timer(300.0, cancel_sos_auto)
        sos_timer.start()
        
        return jsonify({
            "status": "success",
            "sos_active": True,
            "message": "SOS triggered successfully",
            "alerts": {
                "sms": sms_sent,
                "email": email_sent
            }
        })
        
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/sos/cancel', methods=['POST'])
def cancel_sos():
    global sos_active, sos_timer
    
    sos_active = False
    if sos_timer:
        sos_timer.cancel()
        sos_timer = None
    
    return jsonify({"status": "success", "sos_active": False})

def cancel_sos_auto():
    global sos_active, sos_timer
    sos_active = False
    sos_timer = None

@app.route('/api/sos/contacts', methods=['GET', 'POST'])
def manage_contacts():
    global emergency_contacts
    
    if request.method == 'GET':
        return jsonify({"contacts": emergency_contacts})
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            emergency_contacts = data.get('contacts', emergency_contacts)
            with open('emergency_contacts.json', 'w') as f:
                json.dump(emergency_contacts, f)
            return jsonify({"status": "success", "contacts": emergency_contacts})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/geocode', methods=['POST'])
def geocode():
    try:
        data = request.get_json()
        address = data.get('address', '')
        
        url = "https://nominatim.openstreetmap.org/search"
        params = {'q': address, 'format': 'json', 'limit': 1}
        headers = {'User-Agent': 'VisionAssist/1.0'}
        
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        
        if data:
            return jsonify({
                "lat": float(data[0]['lat']),
                "lon": float(data[0]['lon']),
                "display_name": data[0]['display_name']
            })
        else:
            return jsonify({"error": "Location not found"}), 404
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/reverse-geocode', methods=['POST'])
def reverse_geocode():
    try:
        data = request.get_json()
        lat = data.get('lat')
        lon = data.get('lon')
        
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {'lat': lat, 'lon': lon, 'format': 'json'}
        headers = {'User-Agent': 'VisionAssist/1.0'}
        
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        
        return jsonify({"address": data.get('display_name', 'Unknown')})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204

def cleanup():
    global sonar_active, arduino, sos_active, sos_timer
    sonar_active = False
    sos_active = False
    if sos_timer:
        sos_timer.cancel()
    time.sleep(0.5)
    if arduino and arduino.is_open:
        arduino.close()

atexit.register(cleanup)

if __name__ == '__main__':
    print("="*80)
    print("🌟 VISIONASSIST AI - ENHANCED 'MY EYE' UNIVERSAL COMMAND PROCESSOR (W/ FACE & MONEY)")
    print("="*80)
    
    print("\n✅ ENHANCED FEATURES:")
    print("  • Universal Command Processor - All features accessible via 'My Eye'")
    print("  • Smart Command Recognition - Understands natural language")
    print("  • Facial Recognition: 'My Eye, recognize face'")
    print("  • Money Detection: 'My Eye, detect money'")
    print("  • Navigation: 'My Eye, navigate to [place]'")
    print("  • Camera: 'My Eye, start/stop camera'")
    print("  • Detection: 'My Eye, what do you see?'")
    print("  • Sensors: 'My Eye, how far?' / 'how many steps?'")
    print("  • Modes: 'My Eye, normal/rapid mode'")
    print("  • Questions: 'My Eye, what is...' / 'who is...'")
    print("  • Documents: 'My Eye, upload document/image'")
    print("  • Emergency: 'My Eye, emergency' / 'SOS'")
    print("  • Location: 'My Eye, where am I?'")
    print("  • Voice Control: 'My Eye, pause/resume voice'")
    
    print(f"\n📡 Arduino Status: {'CONNECTED' if arduino_connected else 'NOT CONNECTED'}")
    print(f"\n📡 Server: http://localhost:5000")
    print("="*80)
    
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    app.run(debug=False, host='0.0.0.0', port=5000)
