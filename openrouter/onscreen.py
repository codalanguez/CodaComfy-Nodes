"""OpenRouter video with editable casting, blocking and native dialogue."""

from string import Template

from .media import generate_video, list_media


DEFAULT_MODEL = "google/veo-3.1-fast"


SCENE = "$duration_word-second live-action miniature drama filmed at eye level beside a shallow rain puddle on a stone quay. Natural overcast daylight after a shower. Two fictional adults crouch at the water's edge beside a small white folded paper boat. Intimate, gently funny. From the very first frame we can see both people's faces clearly. $a_name, $a_appearance, is on the left; $b_name, $b_appearance, is on the right. Begin in a close two-shot favouring $a_name's face in three-quarter view. $a_name lets go of the boat, looks up at $b_name, and $a_cue in $a_accent: \"$a_line\" $b_name listens silently, looking at the boat. Cut to a matching reverse close-up with $b_name's mouth clearly visible. The boat is already drifting. After a brief beat, $b_name $b_cue in $b_accent: \"$b_line\" Hold briefly on their shared smile. Synchronous on-camera dialogue, clear natural voices, mouth movements belonging only to the current speaker, subtle water patter underneath. $speech_limit No narrator, no subtitles or lettering, no music."


CAST_SCENE = ("$duration_word-second live-action miniature drama filmed at eye level beside a shallow rain puddle "
              "on a stone quay. A small folded paper boat drifts away. Natural overcast daylight and gentle water "
              "patter. Keep the current speaker's face in view. $dialogue $speech_limit No narrator, music or lettering.")
FAIREST_PAIR = ('$duration_word-second shot in the style of the supplied illustration. Keep the ink outlines, '
                'candlelight and open arch. On the left $a_name, $a_appearance, holds out a hand. On the right '
                'stands $b_name, $b_appearance. Show the speaker\'s face as each talks. $a_name $a_cue in '
                '$a_accent: "$a_line" Allow a beat. $b_name $b_cue in $b_accent: "$b_line" $speech_limit '
                'Clear synchronized voices, wind in the arch, no music, captions or lettering.')
FAIREST_CAST = ("$duration_word-second shot in the style of the supplied illustration. Keep the ink outlines, "
                "candlelight and open arch. Preserve the characters' positions from the guide frame. "
                "Show the current speaker's face and leave a beat between lines. $dialogue $speech_limit "
                "Clear synchronized voices, wind in the arch, no music, captions or lettering.")


def character_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def cast_prompt(scene, cast, seconds):
    # Older saved canvases have literal two-person blocking. Adapt those known scenes
    # when the cast changes, while preserving their original two-person performance.
    if len(cast) != 2:
        scene = {SCENE: CAST_SCENE, FAIREST_PAIR: FAIREST_CAST}.get(scene, scene)
    template = Template(scene)
    if not template.is_valid():
        raise ValueError("Invalid scene placeholder. Use $dialogue for the cast and $$ for a literal dollar sign.")
    identifiers = set(template.get_identifiers())
    turns, unplaced, values = [], [], {}
    for index, card in enumerate(cast, 1):
        if not isinstance(card, dict) or not all(key in card for key in ("name", "appearance", "accent", "cue", "line")):
            raise ValueError("Connect an On-Screen Character card to each cast input.")
        slot = chr(96 + index) if index <= 26 else f"speaker{index}"
        values.update({f"{slot}_{key}": value for key, value in card.items()})
        turn = (f"Speaker {index}: {card['name']}. Appearance: {card['appearance']}. "
                f"Accent: {card['accent']}. Performance cue: {card['cue']}. "
                f'Spoken line: "{card["line"]}"')
        turns.append(turn)
        if f"{slot}_line" not in identifiers:
            unplaced.append(turn)
    values["dialogue"] = "\n" + "\n".join(turns) + "\n" if cast else ""
    values["duration_word"] = {"4": "Four", "6": "Six", "8": "Eight"}.get(seconds, str(seconds))
    count = sum(len(speaker["line"].split()) for speaker in cast)
    small_numbers = "zero one two three four five six seven eight nine ten".split()
    values["speech_limit"] = f"Only those {small_numbers[count] if count < len(small_numbers) else count} spoken words."
    try:
        prompt = template.substitute(values)
    except KeyError as exc:
        raise ValueError(f"Scene placeholder ${exc.args[0]} has no connected character or value. "
                         "Use $dialogue to follow the connected cast.") from None
    if "dialogue" not in identifiers and unplaced:
        prompt += "\n" + "\n".join(unplaced)
    if not cast:
        prompt += "\nAn unpopulated scene. No characters, speech or narration. Ambient sound only."
    elif "dialogue" in identifiers or unplaced:
        prompt += ("\nOnly the listed characters speak. Follow the written order and lines exactly. "
                   "Show each speaker delivering their own words with synchronized lip movement.")
    return prompt


class ScreenCharacter:
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"
    RETURN_TYPES = ("CODA_SCREEN_CHARACTER",)
    RETURN_NAMES = ("character",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "name": ("STRING", {"default": "Mara"}),
            "appearance": ("STRING", {"default": "late twenties, short dark curls and a mustard rain jacket"}),
            "accent": ("STRING", {"default": "a light Dublin accent"}),
            "cue": ("STRING", {"default": "asks quietly, hopeful,"}),
            "line": ("STRING", {"default": "Will it float?", "multiline": True, "dynamicPrompts": False}),
        }, "optional": {"previous_characters": ("CODA_SCREEN_CHARACTER", {
            "tooltip": "Connect earlier character cards here to build an ordered cast. This character speaks after them."}),
            "enabled": ("BOOLEAN", {"default": True, "tooltip": "Turn off to pass the previous cast through without this character."})}}

    def run(self, name, appearance, accent, cue, line, previous_characters=None, enabled=True):
        if not enabled:
            return (character_list(previous_characters),)
        if not name.strip() or not line.strip():
            raise ValueError("Give each character a name and a spoken line.")
        card = {"name": name.strip(), "appearance": appearance.strip(), "accent": accent.strip(),
                "cue": cue.strip(), "line": line.strip()}
        return (card if previous_characters is None else [*character_list(previous_characters), card],)


class FrameStack:
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"
    RETURN_TYPES = ("CODA_FRAME_STACK",)
    RETURN_NAMES = ("frames",)

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"enabled": ("BOOLEAN", {"default": True})}, "optional": {
            "previous_frames": ("CODA_FRAME_STACK",),
            "frame": ("IMAGE", {"tooltip": "Append one guide image, or every image in a batch in order."}),
        }}

    def run(self, enabled=True, previous_frames=None, frame=None):
        frames = list(previous_frames or ())
        if enabled and frame is not None:
            frames.extend(frame.split(1, dim=0))
        return (frames,)


class OnScreenDialogue:
    CATEGORY = "Coda/OpenRouter"
    FUNCTION = "run"
    RETURN_TYPES = ("VIDEO",)
    DESCRIPTION = "Generate picture and spoken dialogue together. The picker lists OpenRouter video models that advertise audio generation."

    @classmethod
    def VALIDATE_INPUTS(cls, model=DEFAULT_MODEL):
        return isinstance(model, str) and bool(model.strip()) or "Select a video model."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "scene": ("STRING", {"default": CAST_SCENE, "multiline": True, "dynamicPrompts": False,
                                 "tooltip": "Write camera and action directions. $dialogue inserts all connected cards in order. Individual placeholders such as $a_line and $b_line are also supported."}),
            "seconds": ([str(n) for n in range(1, 31)], {"default": "6"}),
            "take": ("INT", {"default": 1, "min": 1, "max": 999999}),
        }, "optional": {
            "speaker_a": ("CODA_SCREEN_CHARACTER", {"tooltip": "Connect the final character card. Leave open for a scene without characters."}),
            "speaker_b": ("CODA_SCREEN_CHARACTER", {"tooltip": "Optional second speaker. Leave open for a solo performance."}),
            "first_frame": ("IMAGE", {"tooltip": "Optional opening shot. Use a reference image with your cast."}),
            "last_frame": ("IMAGE", {"tooltip": "Optional closing shot, used with a first frame."}),
            "use_last_frame": ("BOOLEAN", {"default": True, "label_on": "on", "label_off": "off",
                                           "tooltip": "Use the ending image. Veo 3.1 uses eight seconds with both frames."}),
            "model": ([DEFAULT_MODEL], {"default": DEFAULT_MODEL}),
            "additional_characters": ("CODA_SCREEN_CHARACTER", {"tooltip": "Third speaker or cast. For more speakers, chain On-Screen Character cards through previous_characters and connect the final card here."}),
            "frame_stack": ("CODA_FRAME_STACK", {"tooltip": "Ordered guides. With use_last_frame on, adjacent frames anchor each shot. Off uses each frame to open its own shot."}),
        }}

    def run(self, speaker_a=None, speaker_b=None, scene=CAST_SCENE, seconds="6", take=1, first_frame=None, last_frame=None,
            use_last_frame=True, model=DEFAULT_MODEL, additional_characters=None, frame_stack=None):
        from comfy_api.latest import InputImpl

        if frame_stack is not None and (first_frame is not None or last_frame is not None):
            raise ValueError("Choose either the frame stack or the separate first and last frame inputs.")
        if frame_stack and use_last_frame and len(frame_stack) > 1:
            shots = list(zip(frame_stack, frame_stack[1:]))
        elif frame_stack:
            shots = [(frame, None) for frame in frame_stack]
        else:
            shots = [(first_frame, last_frame if use_last_frame else None)]
        opening, ending = shots[0]
        if ending is not None and opening is None:
            raise ValueError("Connect a first frame to use an ending frame.")
        if ending is not None and model.startswith("google/veo-3.1"):
            seconds = "8"
        cast = character_list(speaker_a) + character_list(speaker_b) + character_list(additional_characters)
        entry = next((row for row in list_media("speaking_video") if row["id"] == model), None)
        if entry is None:
            raise ValueError(f"{model} is unavailable for native audio. Refresh the dialogue model picker.")
        durations = [n for n in entry.get("supported_durations") or [] if n > 0]
        if durations and int(seconds) not in durations:
            raise ValueError(f"{model} supports these durations in seconds: {durations}.")
        resolutions = entry.get("supported_resolutions") or []
        ratios = entry.get("supported_aspect_ratios") or []
        prompts = []
        count = len(shots)
        for index in range(count):
            # Keep turns contiguous and distribute the remainder to the earliest shots.
            per_shot, extra = divmod(len(cast), count)
            start = index * per_shot + min(index, extra)
            speakers = cast[start:start + per_shot + (index < extra)]
            if count == 1 or not cast:
                prompt = cast_prompt(scene, cast, seconds)
            else:
                # Retain the full cast on screen while assigning each line only once.
                if any(key not in {"dialogue", "duration_word", "speech_limit"}
                       for key in Template(scene).get_identifiers()):
                    raise ValueError("Use $dialogue for multi-shot scenes instead of individual speaker placeholders.")
                staging = "\nCast visible across the sequence: " + "; ".join(
                    f"{card['name']} ({card['appearance']})" for card in cast) + "."
                if speakers:
                    prompt = cast_prompt(scene, speakers, seconds) + staging
                else:
                    prompt = Template(scene).substitute(dialogue="", duration_word=seconds,
                                                        speech_limit="No speech in this shot.") + staging
                    prompt += "\nSilent reaction shot. Nobody speaks."
                prompt += f"\nShot {index + 1} of {count}."
            prompts.append(prompt)
        videos = []
        for prompt, (opening, ending) in zip(prompts, shots):
            buffer = generate_video(model, prompt, int(seconds), "", opening, ending, generate_audio=True,
                                    resolution="720p" if "720p" in resolutions else None,
                                    aspect_ratio="16:9" if "16:9" in ratios else None)
            videos.append(InputImpl.VideoFromFile(buffer))
        return (videos[0] if count == 1 else InputImpl.VideoFromList(videos),)
