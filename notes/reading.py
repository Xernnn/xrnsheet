"""Turn what the note reader says about a staff into tokens, without PyTorch.

The app runs the note reader exported to ONNX, as chords/reading.py explains, and notes/model.py
decodes with this too, in training, so the two ways of running it can't drift apart.
"""

import numpy as np

from chords.reading import Network, best_per_slice, to_arrays
from notes.vocab import BLANK, TOKENS, Reading

WIDTH_REDUCTION = 4  # The convolutions shrink width by this factor.


def decode(log_probabilities: np.ndarray, lengths: np.ndarray) -> list[Reading]:
    """Greedy CTC decoding that also keeps where each token fired.

    A token can fire across several slices, so its position is the middle of that run.
    """
    best_probability, best_index = best_per_slice(log_probabilities)
    readings = []
    for row in range(best_index.shape[0]):
        tokens, xs, confidences = [], [], []
        start = 0
        indices = best_index[row, : int(lengths[row])].tolist()
        probabilities = best_probability[row, : int(lengths[row])].tolist()
        for step in range(1, len(indices) + 1):
            if step < len(indices) and indices[step] == indices[start]:
                continue
            if indices[start] != BLANK:
                tokens.append(TOKENS[indices[start]])
                xs.append((start + step) / 2 * WIDTH_REDUCTION)
                confidences.append(max(probabilities[start:step]))
            start = step
        readings.append(Reading(tokens, xs, confidences))
    return readings


def read_strips(network: Network, strips: list[np.ndarray], batch_size: int = 8) -> list[Reading]:
    """Read staff strips in batches of similar widths, so little of each batch is padding.

    The padding nudges what's read near a strip's right edge, so the batches stay the size they were,
    to read as before."""
    order = sorted(range(len(strips)), key=lambda index: strips[index].shape[1])
    readings: list[Reading | None] = [None] * len(strips)
    for start in range(0, len(order), batch_size):
        chosen = order[start : start + batch_size]
        log_probabilities, lengths = network(*to_arrays([strips[index] for index in chosen]))
        for index, reading in zip(chosen, decode(log_probabilities, lengths)):
            readings[index] = reading
    return readings
