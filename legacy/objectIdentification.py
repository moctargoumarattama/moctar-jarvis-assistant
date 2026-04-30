import cv2
import numpy as np
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input, decode_predictions
import pyttsx3

# Load pre-trained MobileNetV2 model
model = MobileNetV2(weights="imagenet")

# Initialize text-to-speech engine
engine = pyttsx3.init()

# Function to recognize object from image
def recognize_object(image):
    # Preprocess image
    image = cv2.resize(image, (224, 224))
    image = np.expand_dims(image, axis=0)
    image = preprocess_input(image)

    # Predict object classes
    predictions = model.predict(image)
    results = decode_predictions(predictions)

    # Return top prediction
    return results[0][0][1]

# Function to respond to user query about calories
def respond_to_query(object_name):
    # Dummy calorie information (replace with real data)
    calorie_info = {
        "coke": 140,
        "banana": 105,
        "apple": 95,
        # Add more items as needed
    }

    # Get calorie information for the recognized object
    calories = calorie_info.get(object_name.lower())

    # Respond with calorie information
    if calories is not None:
        response = f"The {object_name} has {calories} calories."
    else:
        response = f"Sorry, I don't have calorie information for {object_name}."

    # Speak the response
    engine.say(response)
    engine.runAndWait()

# Initialize camera
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()

    # Show live camera feed
    cv2.imshow('Camera Feed', frame)

    # Press 'q' to quit
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

    # Detect object and respond to query if 'c' is pressed
    if cv2.waitKey(1) & 0xFF == ord('c'):
        object_name = recognize_object(frame)
        respond_to_query(object_name)

        # Display text on the frame
        cv2.putText(frame, f"Object: {object_name}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.imshow('Object Details', frame)
        cv2.waitKey(0)  # Wait for a key press before moving to the next frame

# Release camera and close windows
cap.release()
cv2.destroyAllWindows()
