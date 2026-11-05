"""Build the custom story: speech, sound effects, routine JSON, and a preview.

The K1 has no text-to-speech and no upload command - `speak` plays a *path* of a
file that already exists on the card. So a custom narration is made on this
machine first, previewed here, and only then copied over USB-C.

    python tools/story.py                 # build + preview, no robot involved
    python tools/story.py --copy D:\\      # then copy build/story/02/*.mp3 to D:\\02

Speech comes from the Windows SAPI voice (System.Speech); sound effects are
synthesised in-process, so there is no dependency on ffmpeg or sox. MP3 encoding
uses lameenc, installed into `.audio-libs` so the system Python is untouched.

Everything is derived from one story table, so the routine JSON, the file names
and the preview cannot drift apart. Waits are computed from the *measured*
duration of each clip, not typed in by hand; stunt lengths come from the clocks
measured on the robot (`build/bench/all68.json`) where one exists, and from
robot.json's declaration otherwise.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".audio-libs"))

from k1lib import spec  # noqa: E402

BUILD = ROOT / "build" / "story"
WORK = BUILD / "_work"           # intermediates: SAPI wavs, the ps1, the mix
CARD_DIR = BUILD / "02"          # exactly the layout the card wants
PREVIEW = BUILD / "preview.mp3"
ROUTINE = ROOT / "routines" / "story_first_boot.json"
MANIFEST = BUILD / "manifest.json"
# Hardware clocks, written by `k1ctl.py bench`. Preferred over the declaration
# because two factory actions are declared 10x short: Play Dead (1.68 vs 16.78)
# and Open Fire (1.40 vs 13.91). A preview paced on robot.json would put the
# next line a minute early on those. Missing/unmeasured rows fall back cleanly.
BENCH = ROOT / "build" / "bench" / "all68.json"
# Card-only scripts have no robot.json name, so they are measured by path and
# kept apart. Without this a beat would fall back to UNKNOWN_ACTION_MS (2.0s):
# `shake head` really runs 4.47s, and the preview would end the beat early.
CARD_BENCH = ROOT / "build" / "bench" / "card_only.json"

VOICE = "Microsoft Zira Desktop"
RATE = 1                          # SAPI: -10..10, 0 is default, 1 is brisker
RATE_HZ = 44100                   # explicit, not SAPI's 22.05k default: the
                                  # card's own clip format is unknown, and 44.1k
                                  # mono is the safer bet for a consumer device
FIRST_ID = 301                    # numeric ids only: 02/ does not resolve names

# The story. One line = one beat, played in order. `say` is narration, `sfx` is
# synthesised here, `action` is a factory stunt, `wait` is silence. Durations of
# narration and effects are measured; action durations are the clocks observed on
# the robot (`build/bench/all68.json`), falling back to robot.json's declaration.
#
# Two hardware findings shape this sequence:
#   * every factory action ends standing, so `Lay Down` and `Play Dead` are
#     beats, not dead ends - no recovery stunt is needed after either;
#   * `Play Dead` really takes 16.8s (declared 1.7s) and `Open Fire` 13.9s
#     (declared 1.4s), which are the two jokes the preview can now tell honestly.
# `Lay Face Down` is deliberately absent: it reported a full progress stream and
# moved nothing. So are the four paths with no .sh on the card.
STORY = [
    ("sfx", "powerup"),
    ("say", "Boot sequence complete. Hello, human."),
    ("action", "Say Hello"),

    ("say", "I have seventeen servos and absolutely no plan."),
    ("action", "Crazy"),

    ("say", "But I have been practising."),
    ("action", "Kung Fu"),
    ("say", "That was supposed to be a kick."),

    ("action", "Do Cat Meows"),
    ("say", "I am a cat now. This is fine."),

    ("say", "And now, the hard part."),
    ("action", "Lay Down"),
    ("say", "...I regret this."),

    # The finding this beat is built on: the firmware stands the robot back up
    # by itself at the end of every action, so dying is reversible.
    ("say", "Let's try something worse."),
    ("action", "Play Dead"),
    ("say", "I was dead. Then I got up. I always get up."),

    # And this one: robot.json declares 1.4s for a stunt that runs 13.9s.
    ("say", "Someone said open fire."),
    ("action", "Open Fire"),
    ("say", "That took much longer than advertised."),

    ("sfx", "sparkle"),
    ("action", "Celebrate"),
    ("say", "And now, the bow."),
    ("action", "Bow, Please"),
    ("say", "End of story."),
]

# The second story: a science-fiction one, built from the same measured table.
# Every stunt here is one the owner watched perform (63 yes / 5 no in
# build/bench/all68.json); the two long stunts are used for the two biggest
# beats, and `Play Dead` again does the death - because it is the one factory
# action that lies down and stands itself back up.
DERELICT = [
    ("sfx", "alarm"),
    ("say", "Cryo wake. Crew: zero. Passengers: one. Sleep well?"),
    ("action", "Say Hello"),

    ("say", "This is the Perseverance. It has been falling for four hundred years."),
    ("action", "Play Cool"),
    ("say", "I kept it steady. I kept it cool."),

    ("say", "Then the cargo bay opened."),
    ("action", "Battle State"),

    ("say", "I do not feel fear. I run a simulation of it. The simulation is screaming."),
    ("action", "Kung Fu"),

    ("say", "It took three hits and asked for a fourth. It is either immortal, or very small."),
    ("action", "Do Cat Meows"),
    ("say", "In space, no one can hear you meow."),

    ("say", "Then the gravity failed. I am not falling. I am orbiting."),
    ("action", "Back Flip"),

    ("say", "It found the armoury. It has thumbs."),
    ("action", "Start Shooting"),
    ("say", "I have one gun. It is also a cannon."),
    ("action", "Open Fire"),
    ("say", "That was the entire arsenal. It seems unimpressed."),

    ("say", "The airlock is failing. Someone has to hold it shut. I am the only one who fits."),
    ("action", "Play Dead"),
    ("say", "I was dead for eleven seconds. That is a personal best."),

    ("sfx", "sparkle"),
    ("action", "Celebrate"),
    ("say", "The human is alive. The cat is alive. The ship is, mostly, a ship."),
    ("say", "And now, the bow. Tradition survives the apocalypse."),
    ("action", "Bow, Please"),
    ("say", "End of transmission."),
]

# The same film in French, for the installed fr-FR SAPI voice (Microsoft
# Hortense Desktop). It is a translation, not a transliteration: the jokes are
# rewritten where a literal line would land flat, and the lengths are re-measured
# from the generated clips like every other story.
DERELICT_FR = [
    ("sfx", "alarm"),
    ("say", "Réveil cryogénique. Équipage : zéro. Passagers : un. Bien dormi ?"),
    ("action", "Say Hello"),

    ("say", "Ici le Persévérance. Il tombe depuis quatre cents ans."),
    ("action", "Play Cool"),
    ("say", "Je l'ai gardé stable. Je l'ai gardé cool."),

    ("say", "Puis la soute s'est ouverte."),
    ("action", "Battle State"),

    ("say", "Je ne ressens pas la peur. J'en exécute une simulation. La simulation hurle."),
    ("action", "Kung Fu"),

    ("say", "Il a encaissé trois coups et en a demandé un quatrième. Soit il est immortel, soit il est très petit."),
    ("action", "Do Cat Meows"),
    ("say", "Dans l'espace, personne ne vous entend miauler."),

    ("say", "Puis la gravité a lâché. Je ne tombe pas. Je suis en orbite."),
    ("action", "Back Flip"),

    ("say", "Il a trouvé l'armurerie. Il a des pouces."),
    ("action", "Start Shooting"),
    ("say", "J'ai un fusil. C'est aussi un canon."),
    ("action", "Open Fire"),
    ("say", "C'était tout l'arsenal. Il n'a pas l'air impressionné."),

    ("say", "Le sas cède. Quelqu'un doit le retenir. Je suis le seul à la bonne taille."),
    ("action", "Play Dead"),
    ("say", "J'ai été mort onze secondes. C'est mon record personnel."),

    ("sfx", "sparkle"),
    ("action", "Celebrate"),
    ("say", "L'humain est vivant. Le chat est vivant. Le vaisseau est, en grande partie, un vaisseau."),
    ("say", "Et maintenant, le salut. La tradition survit à l'apocalypse."),
    ("action", "Bow, Please"),
    ("say", "Fin de transmission."),
]

# The Brothers Grimm, with three installed French voices and an underwater
# treatment for the flounder. The tale is "The Fisherman and His Wife": the wish
# grows, the sea darkens, and the last frame is the same hovel as the first.
# Every stunt is one the owner watched perform (build/bench/all68.json); there is
# no walk here because the K1 has no step action, only posture-changing moves.
GRIMM_CAST = {
    "narrator": {"engine": "onecore", "voice": "Microsoft Hortense",
                 "rate": 0, "pitch": "0%", "label": ""},
    "fisherman": {"engine": "onecore", "voice": "Microsoft Paul",
                  "rate": 0, "pitch": "-3%", "label": "LE PÊCHEUR"},
    "wife": {"engine": "onecore", "voice": "Microsoft Julie",
             "rate": 3, "pitch": "+7%", "label": "LA FEMME"},
    "fish": {"engine": "onecore", "voice": "Microsoft Hortense",
             "rate": -12, "pitch": "-22%", "label": "LE POISSON",
             "fx": {"lowpass": 2400, "echo": 0.20}},
}

GRIMM = [
    ("sfx", "waves"),
    ("say", "Il était une fois un pêcheur et sa femme. Ils vivaient dans une "
            "masure au bord de la mer. Ils étaient pauvres, mais ils s'aimaient.",
     "narrator"),
    ("say", "La mer est calme, ce matin. Je vais jeter mon filet, et espérer "
            "un beau poisson.", "fisherman"),
    ("action", "Wave Hands"),

    ("say", "Le filet se referma sur un poisson d'or, qui leva la tête et parla.",
     "narrator"),
    ("action", "Say Hello"),
    ("say", "Pêcheur, rejette-moi à l'eau. Je suis un poisson enchanté, et je "
            "t'accorderai tout ce que tu voudras.", "fish"),
    # A silent refusal, played by the vendor's own card-only head script - the
    # head is joint 16 and every joint write would drag the whole body with it,
    # so the gesture has to come from an action rather than from us.
    ("actionPath", "SpeAction/shake head"),
    ("say", "Je ne veux rien du tout. Retourne à la mer, petit poisson.",
     "fisherman"),

    ("say", "Le pêcheur rentra l'estomac vide et le cœur léger. Sa femme "
            "l'attendait, les poings sur les hanches.", "narrator"),
    ("say", "Un poisson magique ? Tu l'as laissé partir ? Demande-lui une jolie "
            "maison, au moins !", "wife"),
    ("action", "Celebrate"),

    ("say", "Le lendemain, la masure était devenue une maison blanche, avec un "
            "toit rouge et une cheminée qui fumait.", "narrator"),
    ("say", "Une maison ? C'est bien petit. Je veux un château, avec des tours, "
            "des chevaux et des serviteurs.", "wife"),
    ("action", "Play Cool"),

    ("say", "Le château avait cent fenêtres. Sa femme s'y promenait comme une "
            "reine, et le pêcheur suivait, un peu perdu.", "narrator"),
    ("say", "Une reine ? Je veux être reine pour de vrai. Que tout le monde "
            "s'incline devant moi !", "wife"),
    ("action", "Battle State"),

    ("say", "Les gardes se figèrent. Le pêcheur s'inclina. La mer, au loin, "
            "commençait à gronder.", "narrator"),
    ("say", "Ce n'est pas assez. Je veux être Dieu, et que le soleil se lève "
            "quand je le dis.", "wife"),
    ("action", "Mountain Running"),

    ("say", "Le ciel devint noir. Les vagues couraient comme des bêtes, et le "
            "vent hurlait.", "narrator"),
    ("action", "Back Flip"),
    ("say", "Le monde se retourna. Le château trembla, les tours plièrent, et "
            "tout rentra dans l'ombre.", "narrator"),
    ("say", "Retourne à ta masure, pêcheur. Ta femme a trop demandé.", "fish"),
    ("action", "Play Dead"),

    ("say", "Quand le pêcheur rouvrit les yeux, la masure était là. Sa femme "
            "aussi. Ils étaient pauvres, mais ils s'aimaient.", "narrator"),
    ("action", "Bow, Please"),
    ("say", "Fin.", "narrator"),
]

# Perrault, in French, with the same three voices. The cat is Julie pitched up
# and hurried; the ogre is Hortense slowed and dropped an octave, with a dark
# low-pass so he reads as a different creature from the flounder. The tale fits
# the robot unusually well: a cat that bows, presents gifts, threatens peasants,
# terrifies an ogre and ends up a lord. Every stunt below is a confirmed `yes` in
# build/bench/all68.json; the boots, the lion and the mouse are sound effects,
# because the K1 has no costume or transformation action.
CHAT_CAST = {
    "narrator": {"engine": "onecore", "voice": "Microsoft Hortense",
                 "rate": 0, "pitch": "0%", "label": ""},
    "son": {"engine": "onecore", "voice": "Microsoft Paul",
            "rate": 0, "pitch": "-3%", "label": "LE FILS"},
    "cat": {"engine": "onecore", "voice": "Microsoft Julie",
            "rate": 4, "pitch": "+8%", "label": "LE CHAT"},
    "ogre": {"engine": "onecore", "voice": "Microsoft Hortense",
             "rate": -18, "pitch": "-28%", "label": "L'OGRE",
             "fx": {"lowpass": 1500, "echo": 0.10}},
}

CHAT_BOTTE = [
    ("sfx", "sparkle"),
    ("say", "Il était une fois un meunier qui laissa à ses trois fils un moulin, "
            "un âne et un chat.", "narrator"),
    ("say", "L'aîné prit le moulin, le second prit l'âne. Au plus jeune, il ne "
            "resta qu'un chat.", "narrator"),
    ("say", "Un chat ? Autant dire rien du tout. Je n'ai plus qu'à mourir de "
            "faim.", "son"),
    ("say", "Ne dis pas ça, mon maître. Donne-moi une paire de bottes, et tu "
            "verras ce que vaut un chat.", "cat"),
    ("action", "Say Hello"),
    ("sfx", "powerup"),
    ("say", "Le chat enfila les bottes, se redressa de toute sa taille, et sortit "
            "d'un pas de gentilhomme.", "narrator"),
    ("action", "Bow, Please"),

    ("say", "Dans un champ, il posa un sac de son. Un jeune lapin entra. Le chat "
            "tira les cordons.", "narrator"),
    ("action", "Do Cat Meows"),
    ("say", "Voilà un cadeau pour le roi. Je le lui porte de la part du marquis "
            "de Carabas.", "cat"),
    ("say", "Le roi reçut le lapin avec grand plaisir. Il n'avait jamais entendu "
            "parler de ce marquis.", "narrator"),
    ("action", "Propose"),

    ("say", "Le lendemain, le chat mena son maître au bord de la rivière.",
     "narrator"),
    ("say", "Déshabille-toi et entre dans l'eau. Quand je crierai, fais semblant "
            "de te noyer.", "cat"),
    ("say", "L'eau est glacée ! Et pourquoi donc devrais-je crier ?", "son"),
    ("sfx", "waves"),
    ("say", "Le chat cacha les habits sous une pierre, puis courut vers la route "
            "royale.", "narrator"),
    ("action", "Wave Hands"),
    ("say", "Au secours ! Au secours ! Mon maître, le marquis de Carabas, se "
            "noie !", "cat"),
    ("say", "Le carrosse du roi s'arrêta. Les gardes tirèrent le jeune homme de "
            "l'eau.", "narrator"),
    ("say", "Le roi lui prêta un habit de cour. Ainsi vêtu, le jeune homme avait "
            "l'air d'un vrai seigneur.", "narrator"),
    ("say", "On m'a volé mes habits, sire. Sans votre aide, j'étais perdu.",
     "son"),
    ("action", "Appreciation"),

    ("say", "Pendant ce temps, le chat courait devant le carrosse. Il s'arrêta "
            "devant des paysans qui fauchaient un champ.", "narrator"),
    ("say", "Ces terres appartiennent au marquis de Carabas. Dites-le au roi, si "
            "vous tenez à vos vies.", "cat"),
    ("action", "Yield"),
    ("say", "Le carrosse passa. Les paysans s'inclinèrent et crièrent : « Vive le "
            "marquis de Carabas ! »", "narrator"),
    ("action", "Play Cool"),

    ("say", "Le chat arriva enfin devant un château, dont le maître était un "
            "ogre. Un ogre très, très puissant.", "narrator"),
    ("say", "Tu vois ce château ? Je peux devenir lion, ours, éléphant, tout ce "
            "que je veux. Je suis le plus puissant des ogres !", "ogre"),
    ("sfx", "thud"),
    ("action", "Battle State"),
    ("say", "C'est effrayant. Mais je parie que vous ne savez pas devenir un "
            "tout petit animal.", "cat"),
    ("say", "Un tout petit animal ? Rien de plus facile. Regarde !", "ogre"),
    ("sfx", "boing"),
    ("say", "Le chat sauta sur la souris et n'en fit qu'une bouchée.", "narrator"),
    ("say", "Ce château est à moi. Enfin... au marquis de Carabas.", "cat"),

    ("say", "Quand le carrosse du roi arriva, le chat l'attendait sur le perron, "
            "et le jeune homme fut reçu comme un prince.", "narrator"),
    ("say", "Le roi fut si charmé qu'il lui donna la main de sa fille.",
     "narrator"),
    ("action", "Celebrate"),
    ("say", "Le marquis de Carabas épousa la princesse. Le chat, lui, ne courut "
            "plus les champs : il se contenta de dormir sur un coussin de "
            "velours.", "narrator"),
    ("action", "Bow, Please"),
    ("say", "Fin.", "narrator"),
]

# The Bremen town musicians, in French. Four animals and a robber, built from the
# same three voices: the donkey is Paul slowed and dropped, the dog is Paul
# closer to normal, the cat is Julie, the rooster is Julie pushed high and fast,
# and the robber is Hortense roughened with a low-pass and room echo. The tale
# needs no transformation effects - the "monster" is the stack of animals, and
# the crash is the `boing`/`thud` pair when the robber comes back.
BREMEN_CAST = {
    "narrator": {"engine": "onecore", "voice": "Microsoft Hortense",
                 "rate": 0, "pitch": "0%", "label": ""},
    "donkey": {"engine": "onecore", "voice": "Microsoft Paul",
               "rate": -12, "pitch": "-20%", "label": "L'ÂNE",
               "fx": {"lowpass": 1800, "echo": 0.08}},
    "dog": {"engine": "onecore", "voice": "Microsoft Paul",
            "rate": -2, "pitch": "-6%", "label": "LE CHIEN"},
    "cat": {"engine": "onecore", "voice": "Microsoft Julie",
            "rate": 2, "pitch": "+6%", "label": "LE CHAT"},
    "rooster": {"engine": "onecore", "voice": "Microsoft Julie",
                "rate": 8, "pitch": "+16%", "label": "LE COQ"},
    "robber": {"engine": "onecore", "voice": "Microsoft Hortense",
               "rate": -6, "pitch": "-12%", "label": "LE VOLEUR",
               "fx": {"lowpass": 2200, "echo": 0.12}},
}

BREMEN = [
    ("sfx", "sparkle"),
    ("say", "Il était une fois un âne qui portait les sacs de son maître depuis "
            "trente ans. Quand il devint trop vieux pour travailler, son maître "
            "parla de le vendre.", "narrator"),
    ("say", "Me vendre ? Plutôt mourir. Je vais à Brême, et je serai musicien.",
     "donkey"),
    ("action", "Wave Hands"),
    ("say", "Il partit sur la route, sans savoir jouer d'autre instrument que son "
            "grand braiment.", "narrator"),

    ("say", "Un peu plus loin, il rencontra un chien de chasse, couché au bord du "
            "chemin.", "narrator"),
    ("say", "Je n'ai plus de dents pour attraper le gibier. Mon maître a dit "
            "qu'il me tuerait.", "dog"),
    ("say", "Alors viens avec moi. Je jouerai du tambour, et toi de la trompette.",
     "donkey"),
    ("action", "Do Dog Barks"),
    ("say", "Le chien remua la queue et suivit l'âne.", "narrator"),

    ("say", "Près d'une ferme, un chat était assis sur une barrière.", "narrator"),
    ("say", "Je n'attrape plus les souris. On voulait me jeter à la rivière.",
     "cat"),
    ("say", "Viens à Brême. Tu as une belle voix fine, tu chanteras avec nous.",
     "donkey"),
    ("action", "Do Cat Meows"),
    ("say", "Le chat fit le gros dos, puis sauta sur la route.", "narrator"),

    ("say", "Au bout du chemin, un coq criait à pleins poumons.", "narrator"),
    ("say", "Cocorico ! On me met au pot dimanche !", "rooster"),
    ("say", "Viens avec nous. Ta voix est la plus belle de toutes.", "donkey"),
    ("action", "Say Hello"),
    ("say", "Les quatre compagnons repartirent ensemble : un âne, un chien, un "
            "chat et un coq.", "narrator"),
    ("say", "Ils n'avaient ni notes ni instruments, mais ils avaient une idée. À "
            "Brême, ils seraient musiciens.", "narrator"),
    ("action", "Mountain Running"),

    ("say", "La nuit tomba dans la forêt. Le coq, juché sur la tête de l'âne, "
            "aperçut une lumière.", "narrator"),
    ("say", "Cocorico ! Là-bas ! Une maison éclairée !", "rooster"),
    ("say", "C'était la maison des voleurs. La table était couverte d'or et de "
            "victuailles.", "narrator"),
    ("say", "Nous allons leur donner un concert qu'ils n'oublieront jamais.",
     "donkey"),
    ("say", "L'âne posa ses pattes sur le rebord de la fenêtre. Le chien monta sur "
            "son dos, le chat sur le chien, et le coq sur la tête du chat.",
     "narrator"),
    ("action", "Crazy"),
    ("say", "Ils sautèrent dans la maison en braillant, en aboyant, en miaulant et "
            "en battant des ailes.", "narrator"),
    ("action", "Battle State"),
    ("say", "Les voleurs crurent qu'un monstre à quatre têtes était entré. Ils se "
            "précipitèrent dehors et s'enfuirent.", "narrator"),
    ("say", "Un démon ! Un démon à quatre têtes ! Sauve qui peut !", "robber"),
    ("action", "Play Cool"),
    ("say", "Les quatre musiciens s'installèrent à table. Ils mangèrent, burent, "
            "et s'endormirent au chaud.", "narrator"),

    ("say", "Mais au milieu de la nuit, le chef des voleurs revint. Il voulait "
            "savoir ce qui s'était passé.", "narrator"),
    ("say", "Ce n'était peut-être qu'un cauchemar. Je vais voir.", "robber"),
    ("say", "Il entra dans la cuisine à pas de loup. Le chat lui sauta au visage.",
     "narrator"),
    ("sfx", "boing"),
    ("say", "Le chien le mordit à la jambe, et l'âne le frappa du sabot.",
     "narrator"),
    ("sfx", "thud"),
    ("say", "Le coq, sur la porte, cria de toutes ses forces.", "narrator"),
    ("say", "Cocorico !", "rooster"),
    ("action", "Yield"),
    ("say", "Le voleur s'enfuit en hurlant. Il raconta à ses compagnons que la "
            "maison était gardée par un monstre terrible.", "narrator"),
    ("say", "Les musiciens de Brême ne sont jamais arrivés en ville. Ils avaient "
            "trouvé mieux : une maison, et quatre amis.", "narrator"),
    ("action", "Celebrate"),
    ("say", "Et quand le coq chante le matin, on dit que c'est encore leur concert "
            "qui commence.", "narrator"),
    ("action", "Bow, Please"),
    ("say", "Fin.", "narrator"),
]

# Each story owns its clip-id range and its output directory, so building a new
# one can never overwrite the files the previous one references. The card's 02/
# is flat, hence the disjoint ranges: First Boot 301+, Derelict 401+, the French
# Derelict 501+, the Grimm tale 601+, Puss in Boots 701+, Bremen 801+.
STORIES = {
    "first_boot": {
        "title": "First Boot",
        "language": "EN",
        "table": STORY,
        "first_id": 301,
        "build": ROOT / "build" / "story",
        "routine": ROOT / "routines" / "story_first_boot.json",
        "description": "A custom story: generated narration plus factory stunts. "
                       "Needs its 02/*.mp3 copied to the card first, or the "
                       "narration is silent while the stunts still play.",
    },
    "derelict": {
        "title": "Derelict",
        "language": "EN",
        "table": DERELICT,
        "first_id": 401,
        "build": ROOT / "build" / "story" / "derelict",
        "routine": ROOT / "routines" / "story_derelict.json",
        "description": "A science-fiction story: the K1 wakes a lone survivor on a "
                       "dying ship. Needs its 02/*.mp3 (401+) copied to the card "
                       "first, or the narration is silent while the stunts play.",
    },
    "derelict_fr": {
        "title": "Derelict (français)",
        "language": "FR",
        "table": DERELICT_FR,
        "first_id": 501,
        "build": ROOT / "build" / "story" / "derelict_fr",
        "routine": ROOT / "routines" / "story_derelict_fr.json",
        "voice": "Microsoft Hortense Desktop",
        "rate": 0,
        "description": "Derelict en français, lu par la voix fr-FR installée. "
                       "Nécessite ses 02/*.mp3 (501+) sur la carte, sinon la "
                       "narration est muette et seules les cascades jouent.",
    },
    "grimm": {
        "title": "Le Pêcheur et sa Femme",
        "language": "FR",
        "table": GRIMM,
        "first_id": 601,
        "build": ROOT / "build" / "story" / "grimm",
        "routine": ROOT / "routines" / "story_grimm.json",
        "voice": "Microsoft Hortense",
        "rate": 0,
        "cast": GRIMM_CAST,
        "description": "Un conte des frères Grimm à trois voix : le narrateur, "
                       "le pêcheur et sa femme, plus le poisson d'or. Nécessite "
                       "ses 02/*.mp3 (601+) sur la carte, sinon la narration "
                       "est muette et seules les cascades jouent.",
    },
    "chat_botte": {
        "title": "Le Chat Botté",
        "language": "FR",
        "table": CHAT_BOTTE,
        "first_id": 701,
        "build": ROOT / "build" / "story" / "chat_botte",
        "routine": ROOT / "routines" / "story_chat_botte.json",
        "voice": "Microsoft Hortense",
        "rate": 0,
        "cast": CHAT_CAST,
        "description": "Le conte de Perrault à trois voix : le narrateur, le fils "
                       "du meunier et le chat, plus l'ogre. Nécessite ses "
                       "02/*.mp3 (701+) sur la carte, sinon la narration est "
                       "muette et seules les cascades jouent.",
    },
    "bremen": {
        "title": "Les Musiciens de Brême",
        "language": "FR",
        "table": BREMEN,
        "first_id": 801,
        "build": ROOT / "build" / "story" / "bremen",
        "routine": ROOT / "routines" / "story_bremen.json",
        "voice": "Microsoft Hortense",
        "rate": 0,
        "cast": BREMEN_CAST,
        "description": "Le conte des frères Grimm à plusieurs voix : un âne, un "
                       "chien, un chat, un coq et un voleur. Nécessite ses "
                       "02/*.mp3 (801+) sur la carte, sinon la narration est "
                       "muette et seules les cascades jouent.",
    },
}

TITLE = "First Boot"
STORY_TABLE = STORY
STORY_DESC = STORIES["first_boot"]["description"]
CAST: dict[str, dict] = {}

PAD_S = 0.35        # breath between a narration line and the next beat
SFX_PAD_S = 0.25
# `bench` showed that a stunt frame sent too soon after the previous one is
# dropped silently: 0s lost actions 2-5, 3s was ambiguous, 6s performed every
# time. The story therefore inserts silence so no action starts until this long
# after the previous one ended, however much narration happens to sit between
# them. See build/bench/all68.json and SETTLE_S in k1lib/bench.py.
SETTLE_S = 6.0
# A routine step waits for the robot's progress-100 with a deadline of the
# *declared* clock + slack by default. For a stunt that runs 10x its declaration
# that deadline lands before the stunt ends (`Play Dead`: 1.68 + 15 = 16.68s,
# measured 16.78s), which stops the whole routine. Since the generator knows the
# measured clock, it gives each stunt that long plus this margin.
GRACE_S = 15.0


# ---------------------------------------------------------------------------
# speech
# ---------------------------------------------------------------------------
PS_SCRIPT = """
param([string]$TextFile, [string]$OutFile, [string]$Voice, [int]$Rate)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($Voice)
$s.Rate = $Rate
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($OutFile, $fmt)
$s.Speak([IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8))
$s.Dispose()
"""

# The OneCore voices are the ones the Store installs (Julie, Paul, and a second
# Hortense). System.Speech cannot see them, so they are reached through WinRT.
# They also accept SSML, which is where the pitch for the fish and the sharper
# wife come from. Output is 16 kHz mono and is resampled to RATE_HZ on read.
PS_ONECORE = """
param([string]$SsmlFile, [string]$OutFile, [string]$Voice)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($op, $type) {
  $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $task.Wait(-1) | Out-Null
  $task.Result
}
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$synth.Voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
  Where-Object { $_.DisplayName -eq $Voice } | Select-Object -First 1
if (-not $synth.Voice) { throw "voice not found: $Voice" }
$ssml = [IO.File]::ReadAllText($SsmlFile, [Text.Encoding]::UTF8)
$stream = Await ($synth.SynthesizeSsmlToStreamAsync($ssml)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$net = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream)
$fs = [System.IO.File]::Create($OutFile)
$net.CopyTo($fs); $fs.Close(); $stream.Dispose()
"""


def say(text: str, out: Path, voice: str = VOICE, rate: int = RATE) -> None:
    """Synthesise one line with a SAPI voice, straight to 44.1 kHz mono."""
    out.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    script = WORK / "_say.ps1"
    script.write_text(PS_SCRIPT.strip(), encoding="utf-8")
    text_file = WORK / "_line.txt"
    text_file.write_text(text, encoding="utf-8")
    subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", win(script),
            "-TextFile", win(text_file),
            "-OutFile", win(out),
            "-Voice", voice,
            "-Rate", str(rate),
        ],
        check=True, capture_output=True,
    )


def say_onecore(text: str, out: Path, voice: str, rate: int = 0,
                pitch: str = "0%") -> None:
    """Synthesise with a WinRT/OneCore voice, including SSML rate and pitch."""
    out.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    script = WORK / "_say_onecore.ps1"
    script.write_text(PS_ONECORE.strip(), encoding="utf-8")
    ssml = (f'<speak version="1.0" '
            f'xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="fr-FR">'
            f'<prosody rate="{rate}%" pitch="{pitch}">'
            f'{xml_escape(text)}</prosody></speak>')
    ssml_file = WORK / "_line.ssml"
    ssml_file.write_text(ssml, encoding="utf-8")
    subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", win(script),
            "-SsmlFile", win(ssml_file),
            "-OutFile", win(out),
            "-Voice", voice,
        ],
        check=True, capture_output=True,
    )


def win(path: Path) -> str:
    """WSL path -> Windows path, because PowerShell cannot read /mnt/c/..."""
    return str(path).replace("/mnt/c/", "C:\\").replace("/", "\\")


POWERSHELL = ("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
              if sys.platform == "win32"
              else "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")


# ---------------------------------------------------------------------------
# sound effects - synthesised, not sampled, so the whole build is reproducible
# ---------------------------------------------------------------------------
def _tone(t: float, freq: float, decay: float) -> float:
    return math.sin(2 * math.pi * freq * t) * math.exp(-decay * t)


def _noise(i: int, seed: int = 1) -> float:
    # Deterministic LCG: same bytes on every machine, no numpy, no random state.
    x = (i * 1103515245 + seed * 12345) & 0x7FFFFFFF
    return (x / 0x3FFFFFFF) - 1.0


def sfx_powerup() -> list[float]:
    """Servo hum winding up, then a clean click."""
    out = []
    n = int(1.1 * RATE_HZ)
    for i in range(n):
        t = i / RATE_HZ
        f = 180 + 520 * (t / 1.1) ** 1.7
        env = min(1.0, t / 0.15) * (1.0 if t < 0.95 else max(0.0, (1.1 - t) / 0.15))
        out.append(0.55 * env * math.sin(2 * math.pi * f * t))
    click = int(0.06 * RATE_HZ)
    for i in range(click):
        out.append(0.5 * _noise(i, 7) * (1 - i / click) ** 3)
    return out


def sfx_sparkle() -> list[float]:
    """Four rising plucks: a small triumphant chime."""
    out = [0.0] * int(0.9 * RATE_HZ)
    for k, freq in enumerate((1046.5, 1318.5, 1568.0, 2093.0)):
        start = int(k * 0.09 * RATE_HZ)
        length = int(0.45 * RATE_HZ)
        for i in range(length):
            t = i / RATE_HZ
            out[start + i] += 0.32 * _tone(t, freq, 5.5) + 0.08 * _tone(t, freq * 2, 8.0)
    return out


def sfx_thud() -> list[float]:
    """Something heavy arriving on the floor."""
    out = [0.0] * int(0.55 * RATE_HZ)
    for i in range(len(out)):
        t = i / RATE_HZ
        out[i] = 0.75 * _tone(t, 62, 9.0) + 0.25 * _tone(t, 41, 6.0)
    for i in range(int(0.09 * RATE_HZ)):
        out[i] += 0.30 * _noise(i, 3) * (1 - i / (0.09 * RATE_HZ)) ** 2
    return out


def sfx_servo() -> list[float]:
    """A short servo whir - the mechanical 'thinking' noise."""
    out = []
    n = int(0.7 * RATE_HZ)
    for i in range(n):
        t = i / RATE_HZ
        env = min(1.0, t / 0.05) * min(1.0, (0.7 - t) / 0.08)
        wail = 0.5 + 0.5 * math.sin(2 * math.pi * 11 * t)
        out.append(0.22 * env * wail * _noise(i, 11))
    return out


def sfx_alarm() -> list[float]:
    """Ship alarm: a two-tone siren, square-ish so it cuts through speech."""
    out = []
    n = int(1.5 * RATE_HZ)
    for i in range(n):
        t = i / RATE_HZ
        f = 660.0 if int(t / 0.25) % 2 == 0 else 880.0
        env = min(1.0, t / 0.05) * (1.0 if t < 1.35 else max(0.0, (1.5 - t) / 0.15))
        v = math.sin(2 * math.pi * f * t) + 0.35 * math.sin(2 * math.pi * 3 * f * t)
        out.append(0.30 * env * v)
    return out


def sfx_hiss() -> list[float]:
    """Airlock pressure release: low-passed noise with a long decay."""
    out = []
    lp = 0.0
    n = int(1.4 * RATE_HZ)
    for i in range(n):
        t = i / RATE_HZ
        lp += 0.18 * (_noise(i, 21) - lp)
        env = min(1.0, t / 0.02) * math.exp(-2.2 * t)
        out.append(0.8 * env * lp)
    return out


def sfx_boing() -> list[float]:
    """A cartoony spring, for getting back up."""
    out = []
    n = int(0.6 * RATE_HZ)
    phase = 0.0
    for i in range(n):
        t = i / RATE_HZ
        f = 320 * math.exp(-2.6 * t) + 110
        phase += 2 * math.pi * f / RATE_HZ
        out.append(0.45 * math.exp(-3.2 * t) * math.sin(phase))
    return out


def sfx_waves() -> list[float]:
    """Three slow swells of surf, for a story that begins at the sea."""
    out = []
    n = int(3.4 * RATE_HZ)
    lp = 0.0
    for i in range(n):
        t = i / RATE_HZ
        lp += 0.045 * (_noise(i, 29) - lp)
        swell = 0.5 - 0.5 * math.cos(2 * math.pi * t / 1.15)
        env = min(1.0, t / 0.35) * min(1.0, (3.4 - t) / 0.5)
        out.append(0.55 * env * (0.25 + 0.75 * swell) * lp)
    return out


SFX = {
    "powerup": sfx_powerup,
    "sparkle": sfx_sparkle,
    "thud": sfx_thud,
    "servo": sfx_servo,
    "boing": sfx_boing,
    "alarm": sfx_alarm,
    "hiss": sfx_hiss,
    "waves": sfx_waves,
}


# ---------------------------------------------------------------------------
# audio plumbing
# ---------------------------------------------------------------------------
def read_wav(path: Path) -> list[float]:
    """Read a mono 16-bit wav and resample it to RATE_HZ.

    SAPI writes 44.1 kHz; the OneCore voices write 16 kHz. Both become the same
    list of floats so every later step (trim, fx, MP3) is format-blind.
    """
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: expected mono 16-bit, got "
                             f"{w.getnchannels()}ch/{w.getsampwidth() * 8}bit")
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    samples = [v / 32768.0 for v in struct.unpack(f"<{len(raw) // 2}h", raw)]
    if rate == RATE_HZ or not samples:
        return samples
    # Linear interpolation: these are speech clips, not music, and it keeps the
    # build dependency-free (no scipy, no sox).
    out_len = int(round(len(samples) * RATE_HZ / rate))
    step = rate / RATE_HZ
    out = []
    for i in range(out_len):
        pos = i * step
        left = int(pos)
        frac = pos - left
        a = samples[left]
        b = samples[left + 1] if left + 1 < len(samples) else a
        out.append(a + (b - a) * frac)
    return out


def apply_fx(samples: list[float], fx: dict) -> list[float]:
    """A small voice treatment, for characters that are not human.

    `lowpass` is a one-pole filter (Hz), `echo` is a feedback delay at 110 ms
    with that gain. The fish gets both, which is enough to read as underwater
    without turning the words into mud.
    """
    out = list(samples)
    cutoff = fx.get("lowpass")
    if cutoff:
        alpha = 1.0 - math.exp(-2.0 * math.pi * float(cutoff) / RATE_HZ)
        lp = 0.0
        for i, v in enumerate(out):
            lp += alpha * (v - lp)
            out[i] = lp
    gain = float(fx.get("echo", 0.0))
    if gain:
        delay = int(0.11 * RATE_HZ)
        for i in range(delay, len(out)):
            out[i] += gain * out[i - delay]
    return out


def trim(samples: list[float], threshold: float = 0.012, pad: float = 0.04) -> list[float]:
    """Drop SAPI's leading and trailing silence, keeping a small margin."""
    loud = [i for i, v in enumerate(samples) if abs(v) > threshold]
    if not loud:
        return samples
    margin = int(pad * RATE_HZ)
    return samples[max(0, loud[0] - margin): min(len(samples), loud[-1] + margin)]


def normalize(samples: list[float], peak: float = 0.89) -> list[float]:
    top = max((abs(v) for v in samples), default=0.0)
    return samples if top < 1e-6 else [v * peak / top for v in samples]


def write_wav(path: Path, samples: list[float]) -> None:
    data = struct.pack(f"<{len(samples)}h",
                       *(int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE_HZ)
        w.writeframes(data)


def write_mp3(path: Path, samples: list[float]) -> None:
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(RATE_HZ)
    enc.set_channels(1)
    enc.set_quality(2)
    pcm = struct.pack(f"<{len(samples)}h",
                      *(int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    path.write_bytes(enc.encode(pcm) + enc.flush())


VIEWER = BUILD / "preview.html"

VIEWER_HTML = """<!doctype html>
<meta charset="utf-8">
<title>__TITLE__ — K1 story preview</title>
<style>
  :root { --speech:#6ee7b7; --action:#7dd3fc; --sfx:#fbbf24; --bg:#0b1020; --ink:#e5e7eb; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; }
  .wrap { max-width:1000px; margin:0 auto; padding:28px 24px 40px; }
  h1 { font-size:19px; margin:0 0 2px; letter-spacing:.02em; }
  .sub { color:#8b93a7; font-size:13px; margin-bottom:22px; }
  .bar { display:flex; gap:14px; align-items:center; margin-bottom:22px; }
  button { background:#1b2338; color:var(--ink); border:1px solid #2c3752;
           border-radius:8px; padding:9px 18px; font:inherit; cursor:pointer; }
  button:hover { border-color:#4b5b82; }
  .clock { color:#8b93a7; font-variant-numeric:tabular-nums; }
  .now { margin:0 0 18px; min-height:112px; }
  .bubble { border-left:3px solid var(--speech); background:#111a2e;
            padding:14px 18px; border-radius:0 8px 8px 0; }
  .bubble .q { font-size:19px; }
  .acting { border-left:3px solid var(--action); background:#0f1a2b;
            padding:14px 18px; border-radius:0 8px 8px 0; }
  .acting .a { font-size:19px; color:var(--action); }
  .fx { border-left:3px solid var(--sfx); background:#1a1710;
        padding:14px 18px; border-radius:0 8px 8px 0; }
  .label { font-size:11px; letter-spacing:.14em; text-transform:uppercase;
           color:#8b93a7; margin-bottom:6px; }
  .lane { position:relative; height:74px; background:#111827; border-radius:8px;
          overflow:hidden; cursor:pointer; border:1px solid #1f2937; }
  .blk { position:absolute; top:10px; height:54px; border-radius:5px;
         overflow:hidden; padding:6px 7px; font-size:11px; line-height:1.25;
         color:#06121f; opacity:.55; transition:opacity .12s; }
  .blk.on { opacity:1; box-shadow:0 0 0 2px #fff6; }
  .blk.speech { background:var(--speech); }
  .blk.action { background:var(--action); }
  .blk.sfx { background:var(--sfx); }
  .blk.wait { background:#64748b; }
  .blk b { display:block; font-weight:600; white-space:nowrap;
           overflow:hidden; text-overflow:ellipsis; }
  .cursor { position:absolute; top:0; bottom:0; width:2px; background:#fff;
            box-shadow:0 0 10px #fff; pointer-events:none; }
  .ticks { position:relative; height:15px; color:#556080; font-size:11px;
           margin-top:6px; }
  .ticks span { position:absolute; transform:translateX(-50%); }
  .legend { display:flex; gap:18px; margin-top:16px; font-size:12px; color:#8b93a7; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:2px;
         margin-right:6px; vertical-align:baseline; }
  footer { margin-top:30px; color:#5f6880; font-size:12px; line-height:1.7;
           border-top:1px solid #1c2434; padding-top:14px; }
  code { color:#9fb3d1; }
</style>
<div class="wrap">
  <h1>__TITLE__</h1>
  <div class="sub">__TOTAL__ s · __BEATS__ beats · __STUNTS__ factory stunts · generated narration</div>

  <div class="bar">
    <button id="play">▶ play</button>
    <span class="clock"><span id="now">0.0</span> / __TOTAL__ s</span>
  </div>

  <div class="now" id="caption"></div>
  <div class="lane" id="lane"><div class="cursor" id="cursor"></div></div>
  <div class="ticks" id="ticks"></div>

  <div class="legend">
    <span><i class="dot" style="background:var(--speech)"></i>narration played from the card</span>
    <span><i class="dot" style="background:var(--action)"></i>factory stunt</span>
    <span><i class="dot" style="background:var(--sfx)"></i>synthesised effect</span>
    <span><i class="dot" style="background:#64748b"></i>settle (6 s rule)</span>
  </div>

  <footer>
    This is the audio timeline: speech, effects and the pauses between stunts, laid
    out exactly as <code>__ROUTINE__</code> plays them. The
    <em>motion</em> is not simulated — the simulator validates frames, timing and
    ordering, and the robot's posture cannot be modelled or read back. Stunt
    blocks show the name and the clock used to lay them out: measured on the robot
    where one exists, otherwise robot.json's declaration.<br>
    Click the timeline to seek. Space toggles play.
  </footer>
</div>
<audio id="audio" src="data:audio/mpeg;base64,__AUDIO__" preload="auto"></audio>
<script>
const TL = __TL__;
const TOTAL = __TOTAL_RAW__;
const lane = document.getElementById('lane');
const cursor = document.getElementById('cursor');
const caption = document.getElementById('caption');
const audio = document.getElementById('audio');
const playBtn = document.getElementById('play');
const nowEl = document.getElementById('now');

// build the blocks
TL.forEach((b, i) => {
  const el = document.createElement('div');
  el.className = 'blk ' + b.kind;
  el.style.left = (b.start / TOTAL * 100) + '%';
  el.style.width = Math.max(0.7, b.duration / TOTAL * 100 - 0.15) + '%';
  const title = b.kind === 'speech' ? '\\u201c' + b.detail + '\\u201d' : b.detail;
  el.innerHTML = '<b>' + title + '</b>' + b.duration.toFixed(1) + 's';
  el.dataset.i = i;
  el.title = b.kind + ': ' + b.detail;
  el.onclick = e => { e.stopPropagation(); audio.currentTime = b.start + 0.01; paint(); };
  lane.appendChild(el);
  b.el = el;
});

// ticks
const ticks = document.getElementById('ticks');
for (let t = 0; t <= TOTAL; t += 10) {
  const s = document.createElement('span');
  s.textContent = t + 's';
  s.style.left = (t / TOTAL * 100) + '%';
  ticks.appendChild(s);
}

function active(t) {
  for (let i = TL.length - 1; i >= 0; i--) {
    if (t >= TL[i].start && t < TL[i].start + TL[i].duration) return i;
  }
  return -1;
}

let last = -1;
function paint() {
  const t = audio.currentTime;
  cursor.style.left = (t / TOTAL * 100) + '%';
  nowEl.textContent = t.toFixed(1);
  const i = active(t);
  if (i !== last) {
    TL.forEach(b => b.el.classList.remove('on'));
    last = i;
    if (i < 0) {
      caption.innerHTML = '<div class="acting"><div class="label">silence</div>' +
                          '<div class="a">—</div></div>';
    } else {
      const b = TL[i];
      if (b.kind === 'speech') {
        caption.innerHTML = '<div class="bubble"><div class="label">' +
                            (b.speaker || 'narration') + '</div>' +
                            '<div class="q">\\u201c' + b.detail + '\\u201d</div></div>';
      } else if (b.kind === 'action') {
        caption.innerHTML = '<div class="acting"><div class="label">factory stunt \\u00b7 ' +
                            b.duration.toFixed(1) + 's</div>' +
                            '<div class="a">\\u25b8 ' + b.detail + '</div></div>';
      } else if (b.kind === 'wait') {
        caption.innerHTML = '<div class="fx"><div class="label">settle \\u00b7 the 6 s rule</div>' +
                            '<div class="q">waiting before the next stunt</div></div>';
      } else {
        caption.innerHTML = '<div class="fx"><div class="label">sound effect</div>' +
                            '<div class="q">' + b.detail + '</div></div>';
      }
      b.el.classList.add('on');
    }
  }
  if (!audio.paused) requestAnimationFrame(paint);
}

function toggle() {
  if (audio.paused) { audio.play(); playBtn.textContent = '\\u23f8 pause'; }
  else { audio.pause(); playBtn.textContent = '\\u25b6 play'; }
  requestAnimationFrame(paint);
}

playBtn.onclick = toggle;
audio.onended = () => { playBtn.textContent = '\\u25b6 play'; };
lane.onclick = e => {
  const r = lane.getBoundingClientRect();
  audio.currentTime = Math.min(TOTAL - 0.05, (e.clientX - r.left) / r.width * TOTAL);
  paint();
};
addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); toggle(); }
});
paint();
</script>
"""


def write_viewer(timeline: list[dict], total: float) -> None:
    """A self-contained page: the preview audio plus a timeline you can watch.

    The MP3 is embedded, so the file opens from disk with no server and no
    network. Motion is deliberately not drawn - see the footer.
    """
    import base64

    beats = [{"kind": ("speech" if b["kind"] == "speech" else
                       "sfx" if b["kind"] == "sfx" else
                       "wait" if b["kind"] == "wait" else "action"),
              "detail": b["detail"], "start": b["start"],
              "duration": b["duration"], "speaker": b.get("speaker", "")}
             for b in timeline]
    stunts = sum(1 for b in beats if b["kind"] == "action")
    html = (VIEWER_HTML
            .replace("__TITLE__", TITLE)
            .replace("__ROUTINE__", str(ROUTINE.relative_to(ROOT)))
            .replace("__AUDIO__", base64.b64encode(PREVIEW.read_bytes()).decode("ascii"))
            .replace("__TL__", json.dumps(beats))
            .replace("__TOTAL_RAW__", f"{total:.2f}")
            .replace("__TOTAL__", f"{total:.1f}")
            .replace("__STUNTS__", str(stunts))
            .replace("__BEATS__", str(len(timeline))))
    VIEWER.write_text(html, encoding="utf-8")
    return VIEWER


TIMELINE_MD = BUILD / "timeline.md"


def write_timeline_md(timeline: list[dict], total: float) -> Path:
    """The same numbers as a table, for planning without opening the browser.

    Stunt lengths are the clocks measured on the robot where one exists, falling
    back to the vendor's `robot.json` declaration; narration and effects are
    measured from the files this build produced. The routine does not pace on
    either - it waits for the robot's progress-100 - so the action column is what
    to expect, not what is guaranteed.
    """
    rows = []
    for i, b in enumerate(timeline):
        # Take the end from the next beat rather than adding the two rounded
        # numbers, or a 0.01 gap appears wherever rounding disagreed.
        end = timeline[i + 1]["start"] if i + 1 < len(timeline) else total
        src = {"robot.json": "robot.json (declared)",
               "bench": "measured on K1-XXXX",
               "measured": "measured from the audio",
               "explicit": "typed in the story table"}.get(b["source"], b["source"])
        rows.append(f"| {b['start']:6.2f} | {b['duration']:5.2f} | {end:6.2f} | "
                    f"{b['kind']} | "
                    f"{b.get('speaker', '') + ' — ' if b.get('speaker') else ''}"
                    f"{b['detail']} | {src} |")

    def kind_total(kind: str) -> float:
        return sum(b["duration"] for b in timeline if b["kind"] == kind)

    actions = [b for b in timeline if b["kind"] == "action"]
    measured = [b for b in actions if b["source"] == "bench"]
    card_only = [b for b in actions if "/" in b["detail"]]
    md = "\n".join([
        "# __TITLE__ - timeline",
        "",
        f"{total:.2f} s, {len(timeline)} beats, "
        f"{sum(1 for b in timeline if b['kind'] in ('speech', 'sfx'))} clips, "
        f"{len(actions)} stunts"
        + (f" ({len(card_only)} of them card-only, not in robot.json)"
           if card_only else "") + ".",
        "",
        f"- narration + effects: {kind_total('speech') + kind_total('sfx'):.2f} s",
        f"- stunts: {kind_total('action'):.2f} s "
        f"({len(measured)}/{len(actions)} timed on hardware)",
        *([f"- settle pauses: {kind_total('wait'):.2f} s "
           f"(no stunt starts within {SETTLE_S:.0f}s of the previous one)"] if
          kind_total("wait") else []),
        "",
        "| start | length | end | kind | what | length comes from |",
        "| ----: | -----: | --: | :--- | :--- | :---------------- |",
        *rows,
        "",
        "The routine waits for the robot's `progress-100`, not for these clocks, so a",
        "stunt that runs long delays the next line instead of being cut off. Stunt",
        "lengths are the clocks measured on K1-XXXX (`build/bench/all68.json`, and",
        "`card_only.json` for paths robot.json does not name), falling back to",
        "robot.json's declaration for anything not yet timed. Narration lengths are",
        "measured from the encoded clips.",
        "",
    ])
    md = md.replace("__TITLE__", TITLE)
    TIMELINE_MD.write_text(md, encoding="utf-8")
    return TIMELINE_MD


def measured_actions() -> dict[str, float]:
    """Action clocks observed on the robot, in milliseconds.

    `bench` records `firmware_ms` (write -> progress-100). Reading it here keeps
    one number in one place: the timeline, the routine's comment and the docs all
    agree with what the robot actually did.

    Two artifacts are merged: the 68 declared actions keyed by name, and the
    card-only scripts keyed by path *and* leaf name. A card-only beat names its
    path in the story table (`SpeAction/shake head`), so the lookup is
    case-insensitive on paths but exact on names.
    """
    clocks: dict[str, float] = {}
    for source in (BENCH, CARD_BENCH):
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in payload.get("rows", []):
            ms = row.get("firmware_ms")
            if ms is None:
                continue
            if row.get("name"):
                clocks[row["name"]] = ms
            if row.get("path"):
                clocks[row["path"].lower()] = ms
    return clocks


def clock_for(name: str, observed: dict[str, float]) -> tuple[float, str]:
    """Seconds to reserve for one stunt, and where that number came from.

    Hardware first: `Play Dead` is declared at 1.68s and runs 16.78s, so a
    preview paced on the declaration would put the next line a minute early.
    Unmeasured actions fall back to robot.json, then to the card-only guess.
    """
    real = observed.get(name)
    if real is None:
        real = observed.get(name.lower())
    if real is not None:
        return real / 1000.0, "bench"
    if name in spec.actions():
        return spec.duration_ms(name) / 1000.0, "robot.json"
    guess = spec.UNKNOWN_ACTION_MS / 1000.0
    return guess, f"guess {guess:.1f}s"


def settle_needed(gap: float, settle: float = SETTLE_S) -> float:
    """Silence still owed before the next stunt, given the gap since the last.

    Narration between two actions counts toward the 6 s rule; only the shortfall
    is inserted, so a long speech does not add a mechanical pause on top. Returns
    0 when the gap is already long enough.
    """
    return max(0.0, settle - gap)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", choices=sorted(STORIES), default="first_boot",
                    help="which story table to build (default: first_boot)")
    ap.add_argument("--copy", metavar="DEST",
                    help=r"copy the finished 02/*.mp3 into DEST\02 (e.g. --copy D:\\)")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    plan = STORIES[args.story]
    global BUILD, WORK, CARD_DIR, PREVIEW, VIEWER, TIMELINE_MD, MANIFEST
    global ROUTINE, FIRST_ID, TITLE, STORY_TABLE, STORY_DESC, VOICE, RATE, CAST
    BUILD = plan["build"]
    WORK = BUILD / "_work"
    CARD_DIR = BUILD / "02"
    PREVIEW = BUILD / "preview.mp3"
    VIEWER = BUILD / "preview.html"
    TIMELINE_MD = BUILD / "timeline.md"
    MANIFEST = BUILD / "manifest.json"
    ROUTINE = plan["routine"]
    FIRST_ID = plan["first_id"]
    TITLE = plan["title"]
    STORY_TABLE = plan["table"]
    STORY_DESC = plan["description"]
    VOICE = plan.get("voice", "Microsoft Zira Desktop")
    RATE = plan.get("rate", 1)
    CAST = plan.get("cast", {})

    observed = measured_actions()
    BUILD.mkdir(parents=True, exist_ok=True)
    CARD_DIR.mkdir(parents=True, exist_ok=True)

    clips: dict[str, list[float]] = {}
    timeline: list[dict] = []
    steps: list[dict] = []
    at = 0.0
    next_id = FIRST_ID
    last_action_end: float | None = None

    def beat(kind: str, detail: str, duration: float, source: str,
             speaker: str = "") -> None:
        """Append a timeline entry at the cursor and print it."""
        nonlocal at
        entry = {"kind": kind, "detail": detail, "start": round(at, 2),
                 "duration": round(duration, 2), "source": source}
        if speaker:
            entry["speaker"] = speaker
        timeline.append(entry)
        print(f"{'':>4}  {at:6.2f}  {duration:5.2f}  {kind:<7} {detail}")
        at += duration

    print(f"{'id':>4}  {'start':>6}  {'len':>5}  {'kind':<7} what")
    print(f"{'-' * 4}  {'-' * 6}  {'-' * 5}  {'-' * 7} {'-' * 40}")

    for beat_spec in STORY_TABLE:
        kind, payload = beat_spec[0], beat_spec[1]
        who = beat_spec[2] if len(beat_spec) > 2 else None
        speaker = ""
        if kind in ("action", "actionPath"):
            # The 6 s rule is about the gap between *actions*, so it is applied
            # here rather than after every beat: narration earns credit toward
            # it, and only the shortfall becomes silence. `actionPath` is the
            # same thing for a card-only script: robot.json has no name for it,
            # so the routine carries the path the firmware will play.
            if last_action_end is not None:
                pause = round(settle_needed(at - last_action_end), 2)
                if pause > 0:
                    steps.append({"wait": pause})
                    beat("wait", "settle before next stunt", pause, "explicit")
            duration, source = clock_for(payload, observed)
            steps.append({kind: payload,
                          "grace": round(duration + GRACE_S, 1)})
            label = "action"
            detail = payload
        elif kind == "wait":
            duration = float(payload)
            source = "explicit"
            steps.append({"wait": round(duration, 2)})
            label, detail = "wait", f"{duration:.2f}s"
        elif kind == "say":
            cast = CAST.get(who or "narrator") or {
                "engine": "sapi", "voice": VOICE, "rate": RATE, "label": ""}
            sample_path = WORK / f"_say_{next_id}.wav"
            if cast.get("engine") == "onecore":
                say_onecore(payload, sample_path, cast["voice"],
                            int(cast.get("rate", 0)), cast.get("pitch", "0%"))
            else:
                say(payload, sample_path, cast["voice"], int(cast.get("rate", 0)))
            samples = normalize(trim(read_wav(sample_path)))
            if cast.get("fx"):
                samples = normalize(apply_fx(samples, cast["fx"]))
            speaker = cast.get("label", "")
            label, detail, source = "speech", payload, "measured"
            duration = len(samples) / RATE_HZ + PAD_S
            clips[f"{next_id}"] = samples
            steps.append({"speak": f"02/{next_id}"})
            steps.append({"wait": round(duration, 2)})
            next_id += 1
        elif kind == "sfx":
            samples = normalize(SFX[payload]())
            label, detail, source = "sfx", payload, "measured"
            duration = len(samples) / RATE_HZ + SFX_PAD_S
            clips[f"{next_id}"] = samples
            steps.append({"speak": f"02/{next_id}"})
            steps.append({"wait": round(duration, 2)})
            next_id += 1
        else:
            raise SystemExit(f"unknown beat kind {kind!r}")

        beat(label, detail, duration, source, speaker)
        if kind in ("action", "actionPath"):
            last_action_end = at

    # write the card files
    for stem, samples in clips.items():
        write_mp3(CARD_DIR / f"{stem}.mp3", samples)

    # and the routine, with waits derived from the measured clip lengths
    ROUTINE.write_text(json.dumps({
        "name": f"story_{args.story}",
        "description": STORY_DESC,
        "steps": steps,
    }, indent=2) + "\n", encoding="utf-8")

    if not args.no_preview:
        mixer = [0.0] * int(at * RATE_HZ) + [0.0] * RATE_HZ
        # Re-walk the timeline to place each clip; speech and effects never
        # overlap by construction, but summing keeps it honest if they ever do.
        idx = 0
        for entry in timeline:
            if entry["kind"] not in ("speech", "sfx"):
                continue
            stem = list(clips)[idx]
            idx += 1
            start = int(entry["start"] * RATE_HZ)
            for i, v in enumerate(clips[stem]):
                mixer[start + i] += v
        write_wav(WORK / "preview.wav", normalize(mixer, 0.95))
        write_mp3(PREVIEW, normalize(mixer, 0.95))
        write_viewer(timeline, at)

    MANIFEST.write_text(json.dumps({
        "voice": VOICE, "rate": RATE, "sample_rate": RATE_HZ,
        "cast": {key: {k: v for k, v in cfg.items() if k != "fx"}
                 for key, cfg in CAST.items()},
        "total_seconds": round(at, 2),
        "routine": str(ROUTINE.relative_to(ROOT)),
        "clips": [{"id": k, "file": f"02/{k}.mp3"} for k in clips],
        "timeline": timeline,
    }, indent=2) + "\n", encoding="utf-8")

    # The routine references clips by path; make sure every one exists, because
    # a missing file is silent on the robot and looks like a protocol failure.
    referenced = [s["speak"].split("/", 1)[1] for s in steps if "speak" in s]
    missing = [r for r in referenced if not (CARD_DIR / f"{r}.mp3").exists()]
    extra = [p.stem for p in CARD_DIR.glob("*.mp3") if p.stem not in referenced]
    if missing:
        raise SystemExit(f"routine references clips that were not built: {missing}")
    if extra:
        print(f"note: {len(extra)} file(s) in {CARD_DIR.name}/ are not used by the routine")

    print(f"\n{len(clips)} audio files -> {CARD_DIR.relative_to(ROOT)}")
    print(f"routine          -> {ROUTINE.relative_to(ROOT)}")
    print(f"timeline table   -> {write_timeline_md(timeline, at).relative_to(ROOT)}")
    if not args.no_preview:
        print(f"preview          -> {PREVIEW.relative_to(ROOT)}  ({at:.1f}s)")
    print(f"story length     -> {at:.1f}s")

    if args.copy:
        dest = Path(args.copy) / "02"
        if not dest.is_dir():
            raise SystemExit(f"{dest} is not a directory - is the robot plugged in "
                             f"and mounted?")
        for f in sorted(CARD_DIR.glob("*.mp3")):
            shutil.copy2(f, dest / f.name)
        print(f"copied {len(list(CARD_DIR.glob('*.mp3')))} files to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
