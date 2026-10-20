"""Moteur d'Attention Dynamique & Conversation Continue (Dynamic Turn-Taking) pour JARVIS / M.O.C.T.A.R.

Remplace le mot de réveil binaire par une gestion d'attention naturelle :
- Maintient l'écoute collaborative continue (Dynamic Turn-Taking) quand Moctar travaille.
- Distingue chirurgicalement quand Moctar s'adresse à M.O.C.T.A.R ou quand il réfléchit à voix haute ("attends voir...", "voyons pourquoi ça bugue...").
- Ne coupe JAMAIS la réflexion de l'utilisateur, tout en maintenant la session active et attentive.
- Détecte les formules de clôture naturelle ("c'est bon", "laisse tomber", "merci Moctar", "bonne nuit") pour repasser en veille silencieuse.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

LOGGER = logging.getLogger("moctar.dynamic_attention")

DEFAULT_ATTENTION_WINDOW_SECONDS = 45.0
THINKING_EXTENSION_SECONDS = 30.0

# 1. Marqueurs stricts de réflexion à voix haute (Thinking Out Loud / Self-Talk)
_THINKING_PREFIX_PATTERNS = [
    re.compile(r"^(?:euh|hum|heum|ah|eh|ben|bah|tiens|voyons)\b", re.I),
    re.compile(r"^(?:attends|attends\s+voir|attendez|voyons|voyons\s+voir)\b", re.I),
    re.compile(r"^(?:alors\s+l[aà]|bon\s+alors|ouais\s+alors)\b", re.I),
    re.compile(r"^(?:attends\s+(?:une\s+seconde|un\s+peu|deux\s+secondes))\b", re.I),
]

_THINKING_SOLILOQUY_PATTERNS = [
    re.compile(r"\b(?:pourquoi\s+(?:[cç]a|il|elle)\s+(?:bugue?|plante?|marche\s+pas|fonctionne\s+pas))\b", re.I),
    re.compile(r"\b(?:si\s+je\s+(?:fais|change|modifie|teste|mets|lance))\b", re.I),
    re.compile(r"\b(?:je\s+(?:vais\s+tester|regarde|cherche|fais\s+voir|disais\s+quoi))\b", re.I),
    re.compile(r"\b(?:c['’]est\s+bizarre|bizarre\s+ce\s+truc|tiens\s+c['’]est\s+curieux)\b", re.I),
    re.compile(r"\b(?:o[uù]\s+est[- ]ce\s+que\s+j['’]ai\s+mis|comment\s+j['’]avais\s+fait)\b", re.I),
    re.compile(r"\b(?:non\s+c['’]est\s+pas\s+[cç]a|pas\s+[cç]a)\b", re.I),
]

# 2. Marqueurs d'interpellation explicite ou verbes opérationnels à l'impératif (Directed Speech)
_DIRECTED_IMPERATIVES = (
    "ouvre", "ouvrir", "ferme", "fermer", "quitte", "quitter", "lance", "lancer",
    "relance", "relancer", "redémarre", "redemarre", "coupe", "couper", "mets",
    "mettre", "donne", "donner", "dis", "dire", "cherche", "chercher", "trouve",
    "ajoute", "ajouter", "liste", "lister", "nettoie", "optimise", "optimiser",
    "snap", "place", "maximise", "minimise", "aide", "analyse", "vérifie", "verifie",
)

_DIRECTED_INTERROGATIVES = (
    "c'est quoi", "qu'est-ce que", "qu est ce que", "comment", "pourquoi",
    "où en est", "ou en est", "combien", "quel est", "quelle est", "quel temps",
    "quelle heure", "quel jour", "qui est", "qui es-tu",
)

_DISENGAGE_PHRASES = {
    "c'est bon", "c est bon", "c'est tout", "c est tout", "laisse tomber",
    "rien laisse tomber", "merci", "merci moctar", "bonne nuit", "a plus tard",
    "à plus tard", "je coupe", "au revoir", "stop", "pause", "repose-toi",
    "voilà tout ça aujourd'hui", "voila tout ca aujourd'hui", "voilà tout ça", "voila tout ca",
    "voilà c'est tout", "voila c est tout", "voilà c'est tout pour aujourd'hui",
    "c'est tout pour aujourd'hui", "c est tout pour aujourd'hui", "tout ça aujourd'hui",
    "tout ca aujourd'hui", "on a fini", "on a fini pour aujourd'hui", "on s'arrête là",
    "on s arrete la", "fini pour aujourd'hui", "c'est bon pour aujourd'hui",
    "c est bon pour aujourd'hui", "je pense que c'est tout", "je pense que c est tout",
    "bonne soirée", "bonne soiree", "le point est fait",
}

_DISENGAGE_REGEX = re.compile(
    r"^(?:voil[aà],?\s*)?(?:(?:c['’]?est\s+)?tout\s+(?:[cç]a\s+)?(?:pour\s+)?aujourd['’]hui|"
    r"c['’]?est\s+tout(?:\s+pour\s+(?:l['’]instant|aujourd['’]hui))?|"
    r"on\s+(?:a\s+fini|s['’]arr[êe]te\s+l[aà])(?:\s+pour\s+aujourd['’]hui)?|"
    r"fini\s+pour\s+aujourd['’]hui|"
    r"c['’]?est\s+bon(?:\s+pour\s+(?:moi|aujourd['’]hui))?|"
    r"merci(?:\s+beaucoup)?(?:\s+moctar)?|"
    r"bonne\s+(?:nuit|soir[ée]e)|"
    r"[aà]\s+plus(?:\s+tard)?|"
    r"repose[- ]toi)\b",
    re.I,
)

# 3. Marqueurs de fin inachevée / phrase en cours d'énonciation (Predictive Turn-Taking / Semantic VAD)
_TRAILING_INCOMPLETE_CONNECTORS = re.compile(
    r"(?:"
    r"\.\.\.+$|"
    r"…$|"
    r"\b(?:et|ou|mais|donc|car|or|ni)\s*$|"
    r"\b(?:parce\s+qu[e']?|puisqu[e']?|attendu\s+que|vu\s+que)\s*$|"
    r"\b(?:que|qu['’]|qui|dont|o[uù]|si|comme|quand|lorsqu[e']?)\s*$|"
    r"\b(?:pour|avec|dans|sur|sous|vers|chez|sans|pendant|depuis|contre|entre)\s*$|"
    r"\b(?:de|du|des|d['’]|le|la|les|l['’]|un|une)\s*$|"
    r"\b(?:mon|ma|mes|ton|ta|tes|son|sa|ses|notre|votre|leur|ce|cet|cette|ces)\s*$|"
    r"\b(?:je\s+voudrais(?:\s+que)?|je\s+veux(?:\s+que)?|j['’]aimerais(?:\s+que)?)\s*$|"
    r"\b(?:peux[- ]tu|pourrais[- ]tu|est[- ]ce\s+que|dis[- ]moi|fais\s+voir|attends\s+que|il\s+faut\s+que|faut\s+que)\s*$|"
    r"\b(?:que\s+tu|qu['’]on|si\s+tu|si\s+on)\s*$"
    r")",
    re.IGNORECASE,
)


def is_incomplete_utterance(text: str) -> bool:
    """Détecte chirurgicalement si l'énoncé de Moctar est grammaticalement inachevé
    (connecteur de fin, préposition, ellipse ou formule introductive suspendue).
    """
    clean = (text or "").strip()
    if not clean:
        return False
    if clean.endswith("...") or clean.endswith("…"):
        return True
    norm = clean.rstrip(" ,;:-").lower()
    return bool(_TRAILING_INCOMPLETE_CONNECTORS.search(norm))


def classify_utterance_attention(text: str, is_session_active: bool = False) -> Dict[str, Any]:
    """Analyse chirurgicalement si Moctar s'adresse au système ou réfléchit à voix haute.

    Args:
        text: Transcription réparée de la parole.
        is_session_active: Indique si une session collaborative est déjà engagée.

    Returns:
        Dict contenant :
            - intent: 'directed_speech', 'thinking_out_loud', 'disengage', 'ambient'
            - is_directed: bool
            - is_thinking_out_loud: bool
            - is_incomplete: bool
            - should_disengage: bool
            - reason: str
    """
    clean = (text or "").strip()
    if not clean or len(clean) < 2:
        return {
            "intent": "ambient",
            "is_directed": False,
            "is_thinking_out_loud": False,
            "is_incomplete": False,
            "should_disengage": False,
            "reason": "empty_or_too_short",
        }

    norm = clean.lower().strip(" ,.:;!?-'\u2019")

    # 1. Formules de clôture / désengagement explicite
    if norm in _DISENGAGE_PHRASES or _DISENGAGE_REGEX.search(norm) or any(norm.startswith(dp) for dp in ("merci moctar", "c'est bon moctar", "bonne nuit", "voilà tout")):
        return {
            "intent": "disengage",
            "is_directed": True,
            "is_thinking_out_loud": False,
            "is_incomplete": False,
            "should_disengage": True,
            "reason": "explicit_disengage",
        }

    # 2. Commandes prioritaires d'interruption vocale (Barge-In)
    is_interruption = bool(re.search(
        r"\b(?:tais[- ]toi|ferme[- ]la|chut|silence|arr[êe]te(?:\s+de\s+parler)?)\b",
        norm,
    ))
    if is_interruption:
        return {
            "intent": "interruption",
            "is_directed": True,
            "is_thinking_out_loud": False,
            "is_incomplete": False,
            "should_disengage": False,
            "reason": "explicit_interruption",
        }

    # 3. Interpellation explicite avec le nom du copilote ou surnom complice
    has_explicit_wake = bool(re.search(r"\b(?:moctar|jarvis|moc|mokti)\b", norm))
    has_second_person = bool(re.search(r"\b(?:tu\s+|t['’]|peux[- ]tu|pourrais[- ]tu|dis[- ]moi|te\s+)\b", norm))
    is_wake_call = bool(re.search(
        r"\b(?:tu\s+es\s+l[aà]|t['’]es\s+l[aà]|es[- ]tu\s+l[aà]|dis[- ]moi|eh\s+viens\s+voir|mon\s+pote)\b",
        norm,
    ))

    # 3. VAD Sémantique : Détection des phrases inachevées (Turn-Taking Prédictif)
    # Ne JAMAIS couper Moctar s'il est en train de formuler sa pensée ou sa commande.
    if (is_session_active or has_explicit_wake) and is_incomplete_utterance(clean):
        return {
            "intent": "thinking_out_loud",
            "is_directed": False,
            "is_thinking_out_loud": True,
            "is_incomplete": True,
            "should_disengage": False,
            "reason": "incomplete_utterance",
        }

    # 4. Détection de la réflexion à voix haute (Thinking Out Loud) prioritaire en l'absence d'interpellation directe
    if not (has_explicit_wake or has_second_person):
        for p in _THINKING_PREFIX_PATTERNS:
            if p.search(norm):
                return {
                    "intent": "thinking_out_loud",
                    "is_directed": False,
                    "is_thinking_out_loud": True,
                    "is_incomplete": False,
                    "should_disengage": False,
                    "reason": "prefix_hesitation",
                }
        for p in _THINKING_SOLILOQUY_PATTERNS:
            if p.search(norm):
                return {
                    "intent": "thinking_out_loud",
                    "is_directed": False,
                    "is_thinking_out_loud": True,
                    "is_incomplete": False,
                    "should_disengage": False,
                    "reason": "soliloquy_pattern",
                }
        if norm in ("attends", "attends voir", "voyons", "voyons voir", "hum", "euh", "bizarre", "tiens", "ouais", "ah ouais"):
            return {
                "intent": "thinking_out_loud",
                "is_directed": False,
                "is_thinking_out_loud": True,
                "is_incomplete": False,
                "should_disengage": False,
                "reason": "single_hesitation_token",
            }

    # 5. Présence d'un verbe impératif opérationnel
    has_imperative = False
    tokens = [t.strip(" ,.:;!?-") for t in norm.split() if t.strip(" ,.:;!?-")]
    if tokens:
        first_word = tokens[0]
        if first_word in _DIRECTED_IMPERATIVES:
            has_imperative = True
        elif len(tokens) > 1 and tokens[1] in _DIRECTED_IMPERATIVES and first_word in ("moctar", "jarvis", "yo", "dis", "s'il", "stp"):
            has_imperative = True

    # 6. Présence d'une question interrogative directe ou interpellation directe au copilote
    has_interrogative = any(norm.startswith(q) or f" {q}" in norm for q in _DIRECTED_INTERROGATIVES)
    is_direct_query = is_wake_call or bool(re.search(
        r"\b(?:tu\s+es\s+qui|t['’]es\s+qui|qui\s+es[- ]tu|qui\s+tu\s+es|c['’]est\s+quoi\s+ton\s+job|quel\s+est\s+ton\s+job|"
        r"ton\s+job|ton\s+r[ôo]le|tu\s+sers\s+[aà]\s+quoi|tu\s+fais\s+quoi|tu\s+sais\s+faire\s+quoi|"
        r"on\s+(?:va\s+)?passer\s+[aà]\s+ceci|on\s+enchaine|on\s+encha[îi]ne|on\s+continue|peux[- ]tu|pourrais[- ]tu)\b",
        norm,
    ))

    # Si explicitement interpellé (avec 'Moctar', question directe d'identité/rôle ou verbe d'action clair)
    if has_explicit_wake or is_direct_query or (is_session_active and (has_second_person or has_imperative or has_interrogative)):
        return {
            "intent": "directed_speech",
            "is_directed": True,
            "is_thinking_out_loud": False,
            "is_incomplete": False,
            "should_disengage": False,
            "reason": "explicit_directed_speech",
        }

    # 7. Si en session active et phrase d'au moins 2 mots non classée en réflexion
    if is_session_active and len(tokens) >= 2:
        return {
            "intent": "directed_speech",
            "is_directed": True,
            "is_thinking_out_loud": False,
            "is_incomplete": False,
            "should_disengage": False,
            "reason": "engaged_session_followup",
        }

    # Hors session sans marqueur
    return {
        "intent": "ambient",
        "is_directed": False,
        "is_thinking_out_loud": False,
        "is_incomplete": False,
        "should_disengage": False,
        "reason": "ambient_speech_without_wake",
    }


class DynamicAttentionTracker:
    """Gestionnaire d'état de l'attention continue pour M.O.C.T.A.R."""

    def __init__(self, window_seconds: float = DEFAULT_ATTENTION_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        self.attention_until: float = 0.0
        self.last_directed_time: float = 0.0
        self.turn_count: int = 0

    def is_engaged(self) -> bool:
        """Indique si la session d'attention collaborative est actuellement active."""
        return time.monotonic() < self.attention_until

    def engage(self, duration: Optional[float] = None) -> float:
        """Engage ou prolonge la fenêtre d'attention collaborative."""
        dur = duration if duration is not None else self.window_seconds
        now = time.monotonic()
        self.attention_until = now + dur
        self.last_directed_time = now
        self.turn_count += 1
        LOGGER.info("⚡ [ATTENTION ENGAGED]: Session active pour %.1fs (tour #%d)", dur, self.turn_count)
        return self.attention_until

    def extend_for_thinking(self, duration: float = THINKING_EXTENSION_SECONDS) -> float:
        """Prolonge silencieusement la session pendant que Moctar réfléchit à voix haute."""
        now = time.monotonic()
        new_until = now + duration
        if new_until > self.attention_until:
            self.attention_until = new_until
        LOGGER.info("⚡ [ATTENTION EXTENDED (THINKING)]: Écoute maintenue pour %.1fs sans interruption", duration)
        return self.attention_until

    def disengage(self) -> None:
        """Clôture la session et repasse en veille silencieuse."""
        self.attention_until = 0.0
        self.turn_count = 0
        LOGGER.info("⚡ [ATTENTION DISENGAGED]: Retour en veille passive.")
