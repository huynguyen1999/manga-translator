import numpy as np

from manga_translator.rendering.layout.clearance import rendered_masks_conflict


def test_rendered_masks_reserve_typographic_clearance():
    ink = np.ones((4, 4), dtype=np.uint8)
    assert rendered_masks_conflict((0, 0, 4, 4), ink, 20, (6, 0, 10, 4), ink, 20)
    assert not rendered_masks_conflict((0, 0, 4, 4), ink, 20, (10, 0, 14, 4), ink, 20)
