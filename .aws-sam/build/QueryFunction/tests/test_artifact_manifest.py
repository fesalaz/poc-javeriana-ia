"""Verifica la validación del manifest de artefactos (Iteración 4, QAS-I2)."""
import unittest

from src.artifacts.manifest import ManifestValidationError, validate_manifest


def base_webhook_manifest(**overrides):
    data = {
        "artifact_id": "art-1",
        "course_id": "ISIS-1234",
        "name": "Tutor de repaso",
        "artifact_type": "webhook",
        "url": "https://tutor.example.com/webhook",
    }
    data.update(overrides)
    return data


class TestArtifactManifest(unittest.TestCase):
    def test_valid_webhook_manifest(self):
        manifest = validate_manifest(base_webhook_manifest())
        self.assertEqual(manifest.artifact_type, "webhook")

    def test_missing_required_field_raises(self):
        data = base_webhook_manifest()
        del data["url"]
        with self.assertRaises(ManifestValidationError):
            validate_manifest(data)

    def test_invalid_type_raises(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest(base_webhook_manifest(artifact_type="ftp"))

    def test_non_http_url_raises(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest(base_webhook_manifest(url="ftp://tutor.example.com"))

    def test_embed_requires_allowed_domains(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest(base_webhook_manifest(
                artifact_type="embed", url="https://chat.example.com/widget"
            ))

    def test_embed_url_must_match_whitelist(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest(base_webhook_manifest(
                artifact_type="embed", url="https://otrodominio.com/widget",
                allowed_domains=["chat.example.com"],
            ))

    def test_embed_url_matching_whitelist_is_valid(self):
        manifest = validate_manifest(base_webhook_manifest(
            artifact_type="embed", url="https://widget.chat.example.com/w",
            allowed_domains=["chat.example.com"],
        ))
        self.assertEqual(manifest.artifact_type, "embed")


if __name__ == "__main__":
    unittest.main()
