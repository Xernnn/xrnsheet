"""A small CRNN: convolutions see character shapes, an LSTM reads them left to right.

The model outputs one prediction per horizontal slice of the image. CTC training means
nobody has to mark where each character sits: repeated predictions collapse into one
character, and a blank separates real repeats such as the two 1s in "11".
"""

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from chords import reading
from chords.reading import BLANK, CHARACTER_INDEX, CHARACTERS  # noqa: F401 (imported from here elsewhere)

WIDTH_REDUCTION = 4  # The convolutions shrink width by this factor.


def conv_block(channels_in: int, channels_out: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(channels_in, channels_out, 3, padding=1, bias=False),
        nn.BatchNorm2d(channels_out),
        nn.ReLU(inplace=True),
    )


class ChordReader(nn.Module):
    def __init__(self, hidden: int = 192) -> None:
        super().__init__()
        self.features = nn.Sequential(
            conv_block(1, 32),
            nn.MaxPool2d(2),
            conv_block(32, 64),
            nn.MaxPool2d(2),
            conv_block(64, 128),
            conv_block(128, 128),
            nn.MaxPool2d((2, 1)),
            conv_block(128, 192),
            conv_block(192, 192),
            nn.MaxPool2d((2, 1)),
            conv_block(192, 256),
            nn.AdaptiveAvgPool2d((1, None)),
        )
        self.sequence = nn.LSTM(256, hidden, num_layers=2, bidirectional=True, dropout=0.1, batch_first=True)
        self.classify = nn.Linear(hidden * 2, len(CHARACTERS))

    def forward(self, images: torch.Tensor, widths: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Return log probabilities shaped (batch, slices, characters), and slice counts."""
        slices = self.features(images).squeeze(2).permute(0, 2, 1)
        lengths = torch.clamp(widths // WIDTH_REDUCTION, min=1, max=slices.shape[1])
        packed = pack_padded_sequence(slices, lengths.cpu(), batch_first=True, enforce_sorted=False)
        output, _ = self.sequence(packed)
        output, _ = pad_packed_sequence(output, batch_first=True, total_length=slices.shape[1])
        return self.classify(output).log_softmax(dim=2), lengths


def to_batch(images: list[np.ndarray]) -> tuple[torch.Tensor, torch.Tensor]:
    """Stack images of different widths, padding the right side with white."""
    pixels, widths = reading.to_arrays(images)
    return torch.from_numpy(pixels), torch.from_numpy(widths)


def decode(log_probabilities: torch.Tensor, lengths: torch.Tensor) -> list[tuple[str, float]]:
    """Greedy CTC decoding, as chords/reading.py does it for the app."""
    return reading.decode(log_probabilities.numpy(), lengths.numpy())


def load_reader(path, device: str = "cpu") -> ChordReader:
    model = ChordReader()
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True)["model"])
    return model.to(device).eval()


@torch.inference_mode()
def read_images(model: ChordReader, images: list[np.ndarray], device: str = "cpu", batch_size: int = 128) -> list[tuple[str, float]]:
    results = []
    for start in range(0, len(images), batch_size):
        pixels, widths = to_batch(images[start : start + batch_size])
        log_probabilities, lengths = model(pixels.to(device), widths.to(device))
        results.extend(decode(log_probabilities.cpu(), lengths.cpu()))
    return results
