"""Run a trained reader on a page's images without PyTorch, and turn what it says into text.

Each page is read in a process of its own, and importing PyTorch there cost every page about a
second and 300 MB, a third of the time a page took with the note reader here. So the app runs the
readers exported to ONNX beside their .pt files, in onnxruntime, which RapidOCR and homr load
anyway, and which reads them the same (tests/test_exported.py checks). `python -m chords.export`
and `python -m notes.export` write them, and have to be run after training, or the app goes on
reading with the old model.

If a reader's .onnx is missing, or it's asked for on a GPU, it runs in PyTorch as before. The
readers aren't kept in git but as files of a release, so a fresh checkout has neither until
`python3 tools/fetch_models.py` downloads them (models/models.json says which).

The batching and decoding here are also what chords/model.py uses in training, so the two ways of
running a reader can't drift apart. Nothing here imports PyTorch unless it has to.
"""

import importlib
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

from chords.vocab import ALPHABET

FETCH_MODELS = "python3 tools/fetch_models.py"

BLANK = 0
CHARACTERS = ["<blank>", *ALPHABET]
CHARACTER_INDEX = {character: index for index, character in enumerate(CHARACTERS)}

# A network's run: images stacked by to_arrays in, log probabilities shaped (batch, slices, classes)
# and each image's slice count out.
Network = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


def to_arrays(images: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Stack images of different widths, padding the right side with white."""
    widths = np.array([image.shape[1] for image in images], dtype=np.int64)
    longest = int(widths.max())
    pixels = np.full((len(images), 1, images[0].shape[0], longest), 255, dtype=np.uint8)
    for index, image in enumerate(images):
        pixels[index, 0, :, : image.shape[1]] = image
    return pixels.astype(np.float32) / 127.5 - 1.0, widths


def best_per_slice(log_probabilities: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The likeliest class at each slice, and how likely it is."""
    probabilities = np.exp(log_probabilities)
    return probabilities.max(axis=2), probabilities.argmax(axis=2)


def decode(log_probabilities: np.ndarray, lengths: np.ndarray) -> list[tuple[str, float]]:
    """Greedy CTC decoding: take the likeliest character per slice, merge repeats, drop blanks.

    Confidence is the probability of the least certain character the model kept.
    """
    best_probability, best_index = best_per_slice(log_probabilities)
    results = []
    for row in range(best_index.shape[0]):
        previous = BLANK
        characters, probabilities = [], []
        for step in range(int(lengths[row])):
            index = int(best_index[row, step])
            if index != BLANK and index != previous:
                characters.append(CHARACTERS[index])
                probabilities.append(float(best_probability[row, step]))
            previous = index
        results.append(("".join(characters), min(probabilities) if probabilities else 1.0))
    return results


class MissingModel(FileNotFoundError):
    """A reader that isn't in models/, because it hasn't been fetched."""


def has_model(path: Path, device: str = "cpu") -> bool:
    """Whether load_network can load the reader at path: its .onnx, on the CPU, or the .pt itself."""
    return path.exists() or (device == "cpu" and path.with_suffix(".onnx").exists())


def load_network(path: Path, model_module: str, device: str = "cpu") -> Network:
    """Load a trained reader: its exported .onnx beside path in onnxruntime, or path itself in PyTorch.

    model_module names the module whose load_reader loads it in PyTorch, such as "chords.model". It's
    only imported for PyTorch, as it imports PyTorch itself.
    """
    exported = path.with_suffix(".onnx")
    if not has_model(path, device):
        raise MissingModel(f"{exported.name if device == 'cpu' else path.name} isn't in models/. Fetch the readers with: {FETCH_MODELS}")
    if device == "cpu" and exported.exists():
        import onnxruntime

        session = onnxruntime.InferenceSession(str(exported), providers=["CPUExecutionProvider"])
        return lambda pixels, widths: tuple(session.run(None, {"pixels": pixels, "widths": widths}))
    if device == "cpu":
        # Said once a process, as the reader is loaded once; the reading is the same, only slower to start.
        exporter = model_module.replace(".model", ".export")
        print(f"{exported.name} is missing, so {path.name} is read with PyTorch, which is slower to start. Make it with python -m {exporter}.", file=sys.stderr)
    import torch

    model = importlib.import_module(model_module).load_reader(path, device)

    @torch.inference_mode()
    def run(pixels: np.ndarray, widths: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        log_probabilities, lengths = model(torch.from_numpy(pixels).to(device), torch.from_numpy(widths).to(device))
        return log_probabilities.cpu().numpy(), lengths.cpu().numpy()

    return run


def read_images(network: Network, images: list[np.ndarray], batch_size: int = 128) -> list[tuple[str, float]]:
    """Read chord crops. Each is padded to the widest in its batch, and the padding nudges what's read
    near its right edge, so the batches stay the size they were, to read as before."""
    results = []
    for start in range(0, len(images), batch_size):
        log_probabilities, lengths = network(*to_arrays(images[start : start + batch_size]))
        results.extend(decode(log_probabilities, lengths))
    return results
