from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC

# Sample dataset
sentences = [
    "Could you please open the Chrome browser?",
    "Please launch Google Chrome.",
    "Can you start the browser?",
    "Open Firefox, please.",
    "Start the web browser, please.",
]
# Corresponding commands
commands = [
    "open chrome",
    "open chrome",
    "open chrome",
    "open firefox",
    "open chrome",
]

# Feature extraction
vectorizer = TfidfVectorizer(stop_words='english')
print("vec :",vectorizer)
X = vectorizer.fit_transform(sentences)
print("x",X)

# Training the model
model = LinearSVC()
model.fit(X, commands)

# New sentence to predict
new_sentence = "Could you open google?"

# Transform the new sentence using the same vectorizer
new_sentence_vectorized = vectorizer.transform([new_sentence])
print("new sentence",new_sentence_vectorized)
# Predict the corresponding command
predicted_command = model.predict(new_sentence_vectorized)
print("Predicted command:", predicted_command)
