# CodaComfy Nodes

A ComfyUI extension for generating video from a prompt, staging on-screen dialogue, and scoring the finished edit. The pack also includes a LoRA browser with stack controls and a latent image chooser.

## Install the extension

1. Download the [extension ZIP](https://github.com/codalanguez/CodaComfy-Nodes/releases/latest/download/CodaComfy-Nodes.zip). Extract the **CodaComfy-Nodes** folder directly into your ComfyUI installation's `custom_nodes` directory. The file `custom_nodes/CodaComfy-Nodes/__init__.py` should exist. If you already run this pack under the name **CodaComfyTools**, update that installation and keep one copy enabled.
2. Use Python 3.11 or newer. From the extracted folder, run `python -m pip install -r requirements.txt` using ComfyUI's Python interpreter. The video workflows use ComfyUI's built-in video, audio, math, and text overlay nodes. Update ComfyUI if one of those nodes is missing.
3. For the OpenRouter nodes, set `OPENROUTER_API_KEY` in the environment of the ComfyUI server. Restart ComfyUI and reload the canvas. Paid generation uses the account associated with that key.
4. Open **Templates** and look for the Coda pack, or drag a JSON file from **example_workflows** onto the canvas. To check installation, search the node library for **OpenRouter Generate Video**. The **01 - Simple Text to Video** example needs only a prompt and the server's key.

For a Git installation, run the following from the ComfyUI installation directory, then follow steps 2–4. Managers with an **Install via Git URL** option can use the same repository URL.

```shell
git clone https://github.com/codalanguez/CodaComfy-Nodes.git custom_nodes/CodaComfy-Nodes
```

## Seven example workflows

| Example | What to edit |
| --- | --- |
| 01 · Simple text to video | Set the prompt, model and shot length for a silent clip. |
| 02 · Text to video with generated music | Edit the picture and score prompts. The picture determines the music duration. |
| 03 · Text to video with an uploaded song | Upload a song, describe the concept, and choose the shared clip length. The listening model chooses a passage and the edit uses your original recording. |
| 04 · Text to video with three speakers | Edit each voice, line and performance cue. Chain more cast cards or disable a speaker. |
| 05 · Multiscene text dialogue and music | Direct each scene separately, join them, and adjust the score level under the voices. |
| 06 · Multiscene image frames and dialogue | Upload two guides for the first scene and one for the second. Edit the cast or disconnect a frame stack for a text-only shot. |
| 07 · Full book trailer | Upload an opening guide and title artwork. Edit three scenes, cast cards, title, byline, spoken title and score. |

Templates 03, 06 and 07 contain placeholder filenames. Choose your own files with the upload controls before running. [Download just the seven workflow files](https://github.com/codalanguez/CodaComfy-Nodes/releases/latest/download/CodaComfy-Workflows.zip).

### The Fairest book trailer

This 22-second example of the **Full book trailer** workflow joins three scenes set in Thornfield with a narrated title card. One score runs beneath the dialogue. [Watch the finished trailer](https://codemonkeyarts.com/codacomfy-nodes/#book-trailer).

[Download The Fairest workflow and guide image (ZIP)](https://raw.githubusercontent.com/codalanguez/CodaComfy-Nodes/main/docs/examples/codacomfy-the-fairest-workflow.zip). Unzip the bundle, copy `thornfield-guide.jpg` into your ComfyUI `input` folder, and open `The Fairest - Full Book Trailer.json`. Edit the character cards to change who speaks, then change the written and spoken title for your own book.

## Working with the nodes

- **Video and models.** Use **Refresh available models** on a generation node to load the provider catalog. Choose a model, then a supported shot length. Model availability, frame support and native audio vary by provider. The credit control shows the balance reported for the configured account.
- **On-screen dialogue.** Connect zero or more **On-Screen Character** cards in speaking order. Each card carries appearance, accent, performance cue and line. Keep `$dialogue` in the scene text to follow the connected cast automatically. With the cast disconnected, the same node can render a scene without dialogue.
- **Frame guides.** Chain **Frame Stack** cards in story order. One image supplies an opening guide. Consecutive image pairs can generate separate shots and incur separate charges. Choose a model that supports your guide inputs.
- **Sound.** Use **OpenRouter Generate Sound** for a score, **OpenRouter Character Dialogue** for a separate voice track, and **Mix Dialogue and Soundtrack** to preserve voices under music. The uploaded-song template sends a compressed listening copy to the selected model.
- **Local tools.** **LoRA Stack (Preview + Triggers)** builds a stack with adjustable strengths. **Latent Image Chooser (Pause)** pauses a latent batch for a visual selection.

The pack is distributed under the [MIT license](LICENSE).
