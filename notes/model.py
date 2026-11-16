"""A CRNN that reads one staff from left to right, like the chord reader but for music.

The important difference is height. A chord's letters mean the same at any height, so the
chord reader averages height away. Here height is the pitch, so the features keep it: the
image is shrunk to six rows, and all six go into the LSTM.
"""

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from chords import reading as chord_reading
from chords.model import conv_block
from notes import reading
from notes.reading import WIDTH_REDUCTION
from notes.vocab import TOKENS, Reading

ROWS = 6  # A 96 pixel strip after four halvings.
CHANNELS = 192


class NoteReader(nn.Module):
    def __init__(self, hidden: int = 320) -> None:
        super().__init__()
        self.features = nn.Sequential(
            conv_block(1, 32),
            nn.MaxPool2d(2),
            conv_block(32, 64),
            nn.MaxPool2d(2),
            conv_block(64, 128),
            conv_block(128, 128),
            nn.MaxPool2d((2, 1)),
            conv_block(128, CHANNELS),
            conv_block(CHANNELS, CHANNELS),
            nn.MaxPool2d((2, 1)),
        )
        self.squeeze = nn.Sequential(nn.Linear(CHANNELS * ROWS, hidden), nn.ReLU(inplace=True), nn.Dropout(0.1))
        self.sequence = nn.LSTM(hidden, hidden, num_layers=2, bidirectional=True, dropout=0.1, batch_first=True)
        self.classify = nn.Linear(hidden * 2, len(TOKENS))

    def forward(self, images: torch.Tensor, widths: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Return log probabilities shaped (batch, slices, tokens), and slice counts."""
        features = self.features(images)
        batch, channels, rows, slices = features.shape
        columns = features.permute(0, 3, 1, 2).reshape(batch, slices, channels * rows)
        columns = self.squeeze(columns)
        lengths = torch.clamp(widths // WIDTH_REDUCTION, min=1, max=slices)
        packed = pack_padded_sequence(columns, lengths.cpu(), batch_first=True, enforce_sorted=False)
        output, _ = self.sequence(packed)
        output, _ = pad_packed_sequence(output, batch_first=True, total_length=slices)
        return self.classify(output).log_softmax(dim=2), lengths


def to_batch(strips: list[np.ndarray]) -> tuple[torch.Tensor, torch.Tensor]:
    pixels, widths = chord_reading.to_arrays(strips)
    return torch.from_numpy(pixels), torch.from_numpy(widths)


def decode(log_probabilities: torch.Tensor, lengths: torch.Tensor) -> list[Reading]:
    """Greedy CTC decoding that also keeps where each token fired, as notes/reading.py does it for the app."""
    return reading.decode(log_probabilities.numpy(), lengths.numpy())


def load_reader(path, device: str = "cpu") -> NoteReader:
    model = NoteReader()
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True)["model"])
    return model.to(device).eval()


@torch.inference_mode()
def read_strips(model: NoteReader, strips: list[np.ndarray], device: str = "cpu", batch_size: int = 8) -> list[Reading]:
    order = sorted(range(len(strips)), key=lambda index: strips[index].shape[1])
    readings: list[Reading | None] = [None] * len(strips)
    for start in range(0, len(order), batch_size):
        chosen = order[start : start + batch_size]
        pixels, widths = to_batch([strips[index] for index in chosen])
        log_probabilities, lengths = model(pixels.to(device), widths.to(device))
        for index, reading in zip(chosen, decode(log_probabilities.cpu(), lengths.cpu())):
            readings[index] = reading
    return readings
