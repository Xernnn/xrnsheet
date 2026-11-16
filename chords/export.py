"""Export the chord reader to ONNX, which is what the app reads chords with.

    .venv-train/bin/python -m chords.export                 after training the chord reader
    .venv-train/bin/python -m chords.export --name smoke    another model in models/

This writes models/<name>.onnx beside models/<name>.pt. The app reads with the .onnx, in
onnxruntime, to spare each page PyTorch's start (chords/reading.py), so a model trained and not
exported isn't used, and tests/test_exported.py fails until it is. chords.retrain --promote
exports by itself. Exporting needs the onnx package, which only .venv-train has.
"""

import argparse
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from chords.fonts import ROOT
from chords.model import WIDTH_REDUCTION, ChordReader, load_reader, to_batch
from chords.render import HEIGHT

MODEL_DIR = ROOT / "models"


class Exportable(nn.Module):
    """The chord reader as ONNX can take it: the same weights, with its last pooling written as a mean.

    The network averages each column's features down to one row with adaptive pooling, which can't be
    exported while the width is left open. Averaging the rows is the same thing, written another way.
    """

    def __init__(self, reader: ChordReader) -> None:
        super().__init__()
        self.reader = reader

    def forward(self, images: torch.Tensor, widths: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        slices = self.reader.features[:-1](images).mean(dim=2).permute(0, 2, 1)
        lengths = torch.clamp(widths // WIDTH_REDUCTION, min=1, max=slices.shape[1])
        packed = pack_padded_sequence(slices, lengths, batch_first=True, enforce_sorted=False)
        output, _ = self.reader.sequence(packed)
        output, _ = pad_packed_sequence(output, batch_first=True, total_length=slices.shape[1])
        return self.reader.classify(output).log_softmax(dim=2), lengths


def export(network: nn.Module, sample: list[np.ndarray], path: Path) -> None:
    """Write a reader to path, taking any number of images of any width, as the app hands them over.

    The sample is only for tracing the network's steps, so blank images do, if their widths differ.
    """
    torch.onnx.export(
        network.eval(),
        to_batch(sample),
        str(path),
        dynamo=False,  # The older exporter, which needs only the onnx package, not onnxscript as well.
        opset_version=17,
        input_names=["pixels", "widths"],
        output_names=["log_probabilities", "lengths"],
        dynamic_axes={
            "pixels": {0: "batch", 3: "width"},
            "widths": {0: "batch"},
            "log_probabilities": {0: "batch", 1: "slices"},
            "lengths": {0: "batch"},
        },
    )


def export_chord_reader(name: str = "chord_reader") -> Path:
    path = MODEL_DIR / f"{name}.onnx"
    sample = [np.full((HEIGHT, width), 255, np.uint8) for width in (120, 80)]
    export(Exportable(load_reader(MODEL_DIR / f"{name}.pt", "cpu")), sample, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="chord_reader", help="the model in models/, without .pt")
    arguments = parser.parse_args()
    print(f"Wrote {export_chord_reader(arguments.name)}")


if __name__ == "__main__":
    main()
