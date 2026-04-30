from ai_brain import ask_gpt


def chat():
    print("\nSay 'stop listening' to stop")
    while True:
        text = input("You: ")

        if not text:
            continue

        if text.lower() == "stop listening":
            break

        print(ask_gpt(text))
