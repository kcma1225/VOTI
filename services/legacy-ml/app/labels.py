"""Vox-Profile label orders, copied verbatim from the upstream example scripts
(github.com/tiantiaf0627/vox-profile-release @ 85100e6, src/example/*.py). Do not reorder."""

SEX = ["Female", "Male"]                                    # age_sex_wavlm.py: sex_unique_labels

EMOTION = ["Anger", "Contempt", "Disgust", "Fear", "Happiness", "Neutral", "Sadness", "Surprise", "Other"]

ACCENT_BROAD = ["British Isles", "North America", "Other"]

ACCENT_NARROW = [
    "East Asia", "English", "Germanic", "Irish",
    "North America", "Northern Irish", "Oceania",
    "Other", "Romance", "Scottish", "Semitic", "Slavic",
    "South African", "Southeast Asia", "South Asia", "Welsh",
]

VOICE_QUALITY = [
    "shrill", "nasal", "deep",                                              # pitch
    "silky", "husky", "raspy", "guttural", "vocal-fry",                     # texture
    "booming", "authoritative", "loud", "hushed", "soft",                   # volume
    "crisp", "slurred", "lisp", "stammering",                               # clarity
    "singsong", "pitchy", "flowing", "monotone", "staccato", "punctuated", "enunciated", "hesitant",  # rhythm
]

FLUENCY = ["Fluent", "Disfluent"]
DISFLUENCY_TYPES = ["Block", "Prolongation", "Sound Repetition", "Word Repetition", "Interjection"]

# Not a model output: the published age model (wavlm-large-age-sex, apply_reg=True) regresses age/100.
# age_group is derived from that regression using Common Voice's age buckets.
AGE_GROUPS = [(20, "teens"), (30, "twenties"), (40, "thirties"), (50, "forties"), (60, "fifties"),
              (70, "sixties"), (80, "seventies"), (90, "eighties"), (1000, "nineties")]


def age_group(age: float) -> str:
    for upper, name in AGE_GROUPS:
        if age < upper:
            return name
    return AGE_GROUPS[-1][1]
