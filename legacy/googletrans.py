class Result:
    def __init__(self, text):
        self.text = text

class Translator:
    def translate(self, text, dest="en", src="auto"):
        return Result(text)