def switch_voice(engine,voice_id):
    voices = engine.getProperty('voices')
    for voice in voices:
        if voice.id == voice_id:
            engine.setProperty('voice', voice.id)
            break
    if voice_id == 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0':

        text2speech(engine,"Voice change to Male")
    else:
        text2speech(engine,"Voice change to Female")

def text2speech(engine,text):
    user_input = text
    engine.say(user_input)
    engine.runAndWait()
