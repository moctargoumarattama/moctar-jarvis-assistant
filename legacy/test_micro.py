import speech_recognition as sr

print("MICROS DISPONIBLES :")
for i, name in enumerate(sr.Microphone.list_microphone_names()):
    print(i, "-", name)

index = int(input("\nEntre le numéro du micro à tester : "))

r = sr.Recognizer()

with sr.Microphone(device_index=index) as source:
    print("Calibration bruit...")
    r.adjust_for_ambient_noise(source, duration=1)
    print("Parle maintenant pendant 5 secondes...")
    audio = r.listen(source, timeout=10, phrase_time_limit=5)

try:
    text = r.recognize_google(audio, language="fr-FR")
    print("✅ Reconnu :", text)
except Exception as e:
    print("❌ Erreur :", e)