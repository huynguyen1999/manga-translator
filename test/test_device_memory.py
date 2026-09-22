import os
import unittest
from unittest.mock import patch

import torch

from manga_translator.utils.device_memory import (
    configure_device_memory_limits,
    empty_device_cache,
)


class DeviceMemoryTest(unittest.TestCase):
    @patch.object(torch.backends.mps, 'is_available', return_value=True)
    @patch.object(torch.mps, 'synchronize')
    @patch.object(torch.mps, 'empty_cache')
    def test_empty_cache_supports_mps(self, empty_cache, synchronize, _mps_available):
        empty_device_cache('mps')

        empty_cache.assert_called_once_with()
        synchronize.assert_called_once_with()

    @patch.object(torch.backends.mps, 'is_available', return_value=True)
    @patch.object(torch.mps, 'set_per_process_memory_fraction')
    def test_mps_fraction_stays_within_pytorch_range(self, set_fraction, _mps_available):
        with patch.dict(os.environ, {'MANGA_MPS_MEMORY_FRACTION': '1.5'}):
            configure_device_memory_limits('mps')
        set_fraction.assert_not_called()


if __name__ == '__main__':
    unittest.main()
