"""Couche de Réparation Phonétique & Correction d'Erreurs STT pour JARVIS / M.O.C.T.A.R.

Auto-correcteur sémantique et phonétique ultra-rapide (< 1 ms).
Intervient en amont de tout traitement LLM ou routeur pour réparer les transcriptions
acoustiques approximatives produites par Whisper sur les termes techniques,
anglicismes, acronymes et noms propres prononcés avec des accents francophones.

Exemples de réparations :
- "ou VSCOD" / "ou vscod" / "vscot" -> "ouvre VS Code"
- "visuel studio" / "studio code" -> "Visual Studio Code"
- "ou crom" / "ouvre crom" -> "ouvre Chrome"
- "la rame" -> "la RAM"
- "le ces pu" / "c'est pu" / "c'est plus" -> "le CPU"
- "docker com pose" -> "docker compose"
- "git poule" / "git poul" -> "git pull"
- "git comite" / "git comi" -> "git commit"
- "système ctl" / "systeme ctl" -> "systemctl"
- "journal ctl" / "journal ces tl" -> "journalctl"
- "a casat" / "à casat" / "a casa" -> "à Casablanca"
- "a niamé" / "a niame" -> "à Niamey"
- "m'occupe tard" / "m'occaque" / "mokta" -> "Moctar"
- "coupe la zik" / "stop la zik" -> "coupe la musique"
- "pause la zik" / "mets pause" -> "mets la musique en pause"
- "remets le son" / "relance la zik" -> "reprends la musique"
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple, Dict, Any

# ==============================================================================
# 1. DICTIONNAIRE PHONÉTIQUE ET MOTIFS DE RÉPARATION MULTI-MOTS (EXPRESSIONS RÉGULIÈRES)
# ==============================================================================

# Remplacements prioritaires de syntagmes multi-mots (insensible à la casse et aux accents)
_PHONETIC_PHRASE_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # 1. VS Code & Travail
    (re.compile(r"\b(?:ou|ouvr?e?)\s+(?:vs\s*cod[et]?|vscod[et]?)\b", re.I), "ouvre VS Code", "vscode_open"),
    (re.compile(r"\b(?:visuel|visual)\s+studio(?:\s+code)?\b", re.I), "Visual Studio Code", "vscode_canonical"),
    (re.compile(r"\bvs\s*cod[et]?\b", re.I), "VS Code", "vscode_token"),
    (re.compile(r"\b(?:bonne?|mote|mot|monte)\s+bureau\b", re.I), "mode bureau", "work_desktop"),
    (re.compile(r"\b(?:met|mets)[-\s]toi\s+au\s+mode\s+bureau\b", re.I), "mets-toi au mode bureau", "work_mode"),
    (re.compile(r"\bplein\s+ecran\b", re.I), "plein écran", "fullscreen_accent"),
    
    # 2. Chrome & Navigation
    (re.compile(r"\b(?:ou|ouvr?e?|lance)\s+crom\b", re.I), "ouvre Chrome", "chrome_open"),
    (re.compile(r"\b(?:crom|crome)\b", re.I), "Chrome", "chrome_canonical"),
    (re.compile(r"\byou\s*teub\b", re.I), "YouTube", "youtube_typo"),
    (re.compile(r"\b(?:guite|gite)\s*(?:heub|hub|eub)\b", re.I), "GitHub", "github_typo"),
    (re.compile(r"\bbaba\s+mark[eé]\b", re.I), "baba market", "babamarket_typo"),
    
        # 2b. Écosystème & Projets Moctar (Klasora, Babamarket, Heliantha, Noor Express, SupMTI)
    (re.compile(r"\b(?:classe\s+aura|class\s+aura|clasora|clazora)\b", re.I), "Klasora", "klasora_repair"),
    (re.compile(r"\b(?:baba\s*market|baba\s+mark[eé]|baba\s+marqu[eé]s?|babam\s*market)\b", re.I), "Baba Market", "babamarket_repair"),
    (re.compile(r"\b(?:h[eé]lianthe|eliantha|h[eé]liantha|elianthe)\b", re.I), "Heliantha", "heliantha_repair"),
    (re.compile(r"\b(?:nour|noor)\s+express\b", re.I), "Noor Express", "noor_express_repair"),
    (re.compile(r"\b(?:sup|sub)\s*m\s*t\s*i\b", re.I), "SupMTI", "supmti_repair"),

    # 3. CPU, RAM & Matériel
    (re.compile(r"\b(?:la|de\s+la)\s+rame\b", re.I), "la RAM", "ram_repair"),
    (re.compile(r"\b(?:le|du)\s+(?:ces?\s*p[uù]|c['’]?est\s*p[uù]|c['’]?est\s*plus|s[ée]pu)\b", re.I), "le CPU", "cpu_repair"),
    (re.compile(r"\b(?:ces?\s*p[uù]|c['’]?est\s*p[uù]|c['’]?est\s*plus)\b", re.I), "CPU", "cpu_standalone_repair"),
    (re.compile(r"\b(?:temp[ée]rature|chauffe)\s+du\s+ces?\s*p[uù]\b", re.I), "température du CPU", "cpu_temp"),
    (re.compile(r"\ble\s+dis\s+que\s+dur\b", re.I), "le disque dur", "disk_repair"),
    (re.compile(r"\bles\s+dis\s+ques?\b", re.I), "les disques", "disks_repair"),

    # 4. Git & DevOps
    (re.compile(r"\bdocker\s+com\s*pose\b", re.I), "docker compose", "docker_compose"),
    (re.compile(r"\b(?:git|guite|guide|guilde)\s+poul[es]?\b", re.I), "git pull", "git_pull"),
    (re.compile(r"\b(?:git|guite|guide|guilde)\s+(?:comit[es]?|commun[es]?|commit)\b", re.I), "git commit", "git_commit"),
    (re.compile(r"\b(?:git|guite|guide|guilde)\s+(?:push|pouche|bouche)\b", re.I), "git push", "git_push"),
    (re.compile(r"\b(?:git|guite|guide|guilde)\s+st[ae]s\b", re.I), "git status", "git_status"),
    (re.compile(r"\ble\s+paru\s+(?:du|de)\b", re.I), "je parle de", "je_parle_de_repair"),
    (re.compile(r"\b(?:babam|papa)\s*market\b", re.I), "Baba Market", "baba_market_repair"),
    (re.compile(r"\bpour\s+vos\s+marqu[ée]s\b", re.I), "pour Baba Market", "baba_market_vos_marques"),
    (re.compile(r"\b(?:baba|papa)\s+marqu[ée]s?\b", re.I), "Baba Market", "baba_marque_repair"),
    (re.compile(r"\bdelle\s+pr[ée]cision\b", re.I), "Dell Precision", "dell_precision_repair"),
    (re.compile(r"\b(?:syst[èe]me|systeme)\s+ctl\b", re.I), "systemctl", "systemctl_repair"),
    (re.compile(r"\bjournal\s+(?:ces?\s*tl|ctl)\b", re.I), "journalctl", "journalctl_repair"),

    # 5. Multimédia & Audio
    (re.compile(r"\b(?:j['’]?ai|j['’]?avais|je\s+mets?|mets?)\s+you\s*tube\s+en\s+pause\b", re.I), "mets YouTube en pause", "youtube_pause_repair"),
    (re.compile(r"\b(?:coupe|stop|arr[êe]te)\s+(?:la\s+)?(?:zik|sic|musik)\b", re.I), "coupe la musique", "music_stop"),
    (re.compile(r"\b(?:pause\s+(?:la\s+)?(?:zik|musique)|mets?\s+pause)\b", re.I), "mets la musique en pause", "music_pause"),
    (re.compile(r"\b(?:reprends?|remets?|relance)\s+(?:la\s+)?(?:zik|musique)\b", re.I), "reprends la musique", "music_resume"),
    (re.compile(r"\b(?:morceau|zik)\s+suivant[es]?\b", re.I), "morceau suivant", "music_next"),
    (re.compile(r"\b(?:morceau|zik)\s+pr[ée]c[ée]dant[es]?\b", re.I), "morceau précédent", "music_prev"),

    # 5b. Interface HUD 3D, NOC & Globe (Whisper réparations phonétiques)
    (re.compile(r"\b(?:return|retourne?|retour)\s+(?:arm\s+|in\s+|on\s+|en\s+|au\s+)?mode\s+(?:noc|nock|noque)\b", re.I), "retourne en mode NOC", "noc_mode_return"),
    (re.compile(r"\b(?:c['’]?est\s+(?:tout\s+|un\s+)?(?:en\s+)?)?(?:mode\s+)(?:hot[- ]?trading|hard[- ]?3\s*d|odd[- ]?3\s*d|old[- ]?3\s*d)\b", re.I), "mode HUD 3D", "hud_3d_hot_trading"),
    (re.compile(r"\b(?:c['’]?est\s+tout\s+en\s+)?(?:mode\s+)?(?:hope|hop|up|hot|hub|eude|ude)\s*3\s*d\b", re.I), "mode HUD 3D", "hud_3d_repair"),
    (re.compile(r"\b(?:mode\s+)?(?:hope|hop|up|hot|hub)\s*3\s*d\b", re.I), "mode HUD 3D", "hud_3d_short"),
    (re.compile(r"\b(?:ouvre|lance|affiche|active)\s+(?:le\s+)?(?:hope|hop|up|hot|hub)\s*3\s*d\b", re.I), "ouvre le HUD 3D", "hud_3d_open"),

    # 6. Météo & Villes courantes
    (re.compile(r"\b(?:[aà]|au)\s+casat?\b", re.I), "à Casablanca", "city_casa"),
    (re.compile(r"\b(?:[aà]|au)\s+(?:niam[eé]|nyamey)\b", re.I), "à Niamey", "city_niamey"),

    # 7b. Contrôles radio & Présence
    (re.compile(r"\b(?:tu\s+m['’][ée]loignes?\s+de\s+(?:soi|toi)|tu\s+m['’][ée]revois|tu\s+m['’]envoies?\s+de\s+(?:soi|toi)|tu\s+me\s+revois|tu\s+m['’]aper[cç]ois|t['’]es\s+r[ée][cç]ois|tu\s+r[ée][cç]ois|t['’]es\s+recois|tu\s+m['’]re[cç]ois|tu\s+m['’]erevois|tu\s+m['’]eloignes?\s+de\s+(?:soi|toi))\b", re.I), "tu me reçois", "wake_radio_recois"),
    (re.compile(r"\b(?:tu\s+m['’][ée]coutes?\s+toi|tu\s+m['’]entends?\s+l[aà])\b", re.I), "tu me reçois", "wake_radio_entends"),
    (re.compile(r"\b(?:je\s+suis\s+qui,?\s+toi|tu\s+m['’][ée]lus\s+toi)\b", re.I), "tu es qui toi", "wake_identity_misheard"),
    (re.compile(r"\b(?:pourquoi\s+tu\s+(?:ne\s+)?me\s+r[ée]compenses\s+depuis)\b", re.I), "pourquoi tu ne me réponds pas depuis", "frustration_recompenses"),
    (re.compile(r"\b(?:mode\s+)?(?:hib|hip|heep|hube)\s*3\s*d\b", re.I), "mode HUD 3D", "hud_3d_hib"),
    (re.compile(r"\b(?:c['’]est\s+(?:le\s+)?)?m(?:\.|\s)*o(?:\.|\s)*c(?:\.|\s)*t(?:\.|\s)*a(?:\.|\s)*r(?:\.|\b)", re.I), "Moctar", "wake_moctar_acronym"),
    (re.compile(r"\b(?:m['’\s]?occupe\s+tard|moccupe\s+tard|m['’\s]?occaque|moccaque)\b", re.I), "Moctar", "wake_moctar_repair"),
    (re.compile(r"\b(?:mokta|moktar|mokhtar|mouctar|mouktar|motard|mothar|mothard|mocthar|mocktab|moctab|moktab)\b", re.I), "Moctar", "wake_moctar_variants"),
    (re.compile(r"\b(?:yo\s+)?(?:moc|mok|moque)\b", re.I), "Moc", "wake_moc"),
    (re.compile(r"\b(?:t['’]es\s+l[aà]|tu\s+es\s+l[aà])\b", re.I), "tu es là", "wake_are_you_there"),

    # 8. Questions directes d'identité, de rôle & transitions (Whisper corrections observées en conditions réelles)
    (re.compile(r"\b(?:j['’]?ai\s+qui,?\s+toi|rikitoa|t['’]es\s+qui,?\s+toi|t\s+es\s+qui,?\s+toi)\b", re.I), "tu es qui toi", "identity_who_are_you"),
    (re.compile(r"\b(?:qu['’]en|quand|c['’]est|l[aà],?\s+le\s+nom\s+de\s+ce)(?:\s+quoi)?(?:\s+ton)?\s+job\b", re.I), "c'est quoi ton job", "identity_job_repair"),
    (re.compile(r"\bc['’]est\s+quoi\s+ton\s+djob\b", re.I), "c'est quoi ton job", "identity_job_spelling"),
    (re.compile(r"\bm[ée]t[ée]o\s*,?\s*mode\s+bureau\b", re.I), "mets-toi en mode bureau", "meteo_mode_bureau_repair"),
    (re.compile(r"\b(?:active|mets)[-\s]le\s+mode\s+bureau\b", re.I), "active le mode bureau", "activate_desktop_mode"),
    (re.compile(r"\bet\s+on\s+va\s+passer\s+[aà]\s+ceci\b", re.I), "on va passer à ceci", "transition_next_step"),

    # 9. Commandes d'interruption vocale (Barge-In)
    (re.compile(r"\b(?:arr[êe]te\s+de\s+parler|tais[- ]toi|ferme[- ]la|chut|silence)\b", re.I), "tais-toi", "interruption_silence"),
    (re.compile(r"\b(?:attends?\s*(?:voir)?)\b", re.I), "attends", "interruption_wait"),
]

# Remplacements de mots uniques individuels
_PHONETIC_WORD_MAP: Dict[str, str] = {
    "vscod": "VS Code",
    "vscode": "VS Code",
    "vscot": "VS Code",
    "crom": "Chrome",
    "crome": "Chrome",
    "giteube": "GitHub",
    "gogol": "Google",
    "gougueule": "Google",
    "sépu": "CPU",
    "cepu": "CPU",
    "sepu": "CPU",
    "ramme": "RAM",
    "nock": "NOC",
    "noque": "NOC",
    "systemctl": "systemctl",
    "journalctl": "journalctl",
    "terminaux": "terminal",
    "eude": "HUD",
        "ude": "HUD",
    "klasora": "Klasora",
    "babamarket": "Babamarket",
    "heliantha": "Heliantha",
    "supmti": "SupMTI",
    "mokhtar": "Moctar",
    "moktar": "Moctar",
}


def _strip_accents(value: str) -> str:
    """Normalise les accents tout en préservant la structure."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )


def repair_stt_transcript(raw_text: str) -> Tuple[str, List[Dict[str, str]]]:
    """Répare phonétiquement et sémantiquement une transcription brute de Whisper (< 1 ms).

    Args:
        raw_text: Texte brut retourné par Whisper (ex: "M. Mokhtar, ou vscod et donne la rame").

    Returns:
        Tuple (texte_réparé, liste_des_corrections_appliquées).
    """
    if not raw_text or not raw_text.strip():
        return "", []

    text = raw_text.strip()
    repairs_applied: List[Dict[str, str]] = []

    # 1. Remplacement des syntagmes multi-mots prioritaires
    for pattern, replacement, label in _PHONETIC_PHRASE_PATTERNS:
        matches = list(pattern.finditer(text))
        if matches:
            for match in matches:
                repairs_applied.append({
                    "rule": label,
                    "matched": match.group(0),
                    "replaced_with": replacement,
                })
            text = pattern.sub(replacement, text)

    # 2. Remplacement au niveau des tokens uniques
    words = text.split()
    repaired_words: List[str] = []
    word_modified = False

    for word in words:
        clean_w = word.strip(" ,.:;!?-'\u2019").lower()
        if clean_w in _PHONETIC_WORD_MAP:
            target = _PHONETIC_WORD_MAP[clean_w]
            # Conserver la ponctuation attachée au mot
            leading = word[:len(word) - len(word.lstrip(" ,.:;!?-'\u2019"))]
            trailing = word[len(word.rstrip(" ,.:;!?-'\u2019")):]
            repaired_words.append(f"{leading}{target}{trailing}")
            repairs_applied.append({
                "rule": "word_token",
                "matched": word,
                "replaced_with": target,
            })
            word_modified = True
        else:
            repaired_words.append(word)

    if word_modified:
        text = " ".join(repaired_words)

    # 3. Nettoyage des espaces multiples
    text = re.sub(r"\s+", " ", text).strip()
    return text, repairs_applied
