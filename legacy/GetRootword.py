import nltk
from nltk.corpus import wordnet
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize  



def remove_stopwords(cmd):  # stop word remover

    words = word_tokenize(cmd)
    stop_words = set(stopwords.words('english'))
    filtered_words = [word for word in words if word.lower() not in stop_words]
    filtered_text = ' '.join(filtered_words)
    print("Original sentence:", cmd)
    print("After removing stopwords:", filtered_text)
    return filtered_text


def get_similar_words(word):
    print("Getting similar words ... ")
    synonyms = [word]
    for syn in wordnet.synsets(word):
        for lemma in syn.lemmas():
            synonyms.append(lemma.name())
    print("similar wors for ",word," is ",synonyms)
    return synonyms


def get_root_word(similarwords):
    print("get root word for the similar words",similarwords[0])
    lst = ["open", "start","close", "chrome", "camera"]
    for word in lst:
        if word in similarwords:
            print(word)
            return word


def main():
    cmd = input("Enter the cmd ")
    print(cmd)
    word_af_remove = remove_stopwords(cmd)
    words = word_af_remove.split()
    rootword = []
    for word in words:
        similarwords = get_similar_words(word)
        rootword.append(get_root_word(similarwords))
    print(rootword)


main()
