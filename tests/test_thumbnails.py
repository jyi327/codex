from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch
from PIL import Image
import thumbnails


class ThumbnailTests(unittest.TestCase):
    def test_image_is_decoded_and_fitted_to_video_shape(self):
        data = BytesIO()
        Image.new('RGB', (320, 180), 'blue').save(data, format='JPEG')
        response = MagicMock()
        response.read.return_value = data.getvalue()
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(thumbnails, 'build_opener', return_value=opener):
            picture = thumbnails.fetch('abcdefghijk')
        self.assertEqual(picture.size, (160, 90))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg')

    def test_invalid_ids_and_oversized_payloads_are_rejected(self):
        with self.assertRaises(ValueError):
            thumbnails.fetch('../anything')
        response = MagicMock()
        response.read.return_value = b'x' * 1_000_001
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(thumbnails, 'build_opener', return_value=opener), self.assertRaises(ValueError):
            thumbnails.fetch('abcdefghijk')
