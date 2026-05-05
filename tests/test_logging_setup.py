import io
import logging
import os
import tempfile
import unittest
from unittest import mock

from app import logging_setup


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.Logger("test-clipboard-logging")
        self.logger.propagate = False

    def tearDown(self):
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()

    def app_handlers(self):
        return [
            handler for handler in self.logger.handlers
            if getattr(handler, logging_setup.APP_HANDLER_ATTR, False)
        ]

    def test_configure_logging_writes_to_rotating_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "nested", "clipboard_history.log")

            logging_setup.configure_logging(log_path, console=False, logger=self.logger)
            self.logger.warning("file sink works")
            for handler in self.app_handlers():
                handler.flush()

            with open(log_path, encoding="utf-8") as log_file:
                contents = log_file.read()
            logging_setup._remove_app_handlers(self.logger)

        self.assertIn("WARNING", contents)
        self.assertIn("file sink works", contents)

    def test_reconfigure_replaces_only_app_handlers(self):
        external_handler = logging.StreamHandler(io.StringIO())
        self.logger.addHandler(external_handler)

        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = os.path.join(temp_dir, "first.log")
            second_path = os.path.join(temp_dir, "second.log")

            logging_setup.configure_logging(first_path, console=False, logger=self.logger)
            first_handlers = self.app_handlers()
            self.assertEqual(1, len(first_handlers))

            logging_setup.configure_logging(second_path, console=False, logger=self.logger)
            second_handlers = self.app_handlers()
            logging_setup._remove_app_handlers(self.logger)

        self.assertIn(external_handler, self.logger.handlers)
        self.assertEqual(1, len(second_handlers))
        self.assertIsNot(first_handlers[0], second_handlers[0])

    def test_rotation_parameters_are_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "clipboard_history.log")

            logging_setup.configure_logging(
                log_path,
                console=False,
                max_bytes=1234,
                backup_count=7,
                logger=self.logger,
            )
            handler = self.app_handlers()[0]
            self.assertEqual(1234, handler.maxBytes)
            self.assertEqual(7, handler.backupCount)
            logging_setup._remove_app_handlers(self.logger)

    def test_file_handler_failure_falls_back_without_raising(self):
        stream = io.StringIO()

        with (
            mock.patch.object(
                logging_setup,
                "RotatingFileHandler",
                side_effect=OSError("cannot open"),
            ),
            mock.patch("sys.stderr", stream),
        ):
            logging_setup.configure_logging(
                r"C:\missing\clipboard_history.log",
                console=True,
                logger=self.logger,
            )
            self.logger.warning("fallback message")
            for handler in self.app_handlers():
                handler.flush()

        self.assertIn("Failed to configure file logging", stream.getvalue())
        self.assertIn("fallback message", stream.getvalue())

    def test_null_handler_is_added_when_file_and_console_are_unavailable(self):
        with mock.patch.object(
            logging_setup,
            "RotatingFileHandler",
            side_effect=OSError("cannot open"),
        ):
            logging_setup.configure_logging(
                r"C:\missing\clipboard_history.log",
                console=False,
                logger=self.logger,
            )

        handlers = self.app_handlers()
        self.assertEqual(1, len(handlers))
        self.assertIsInstance(handlers[0], logging.NullHandler)

    def test_console_flag_controls_stream_handler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = os.path.join(temp_dir, "clipboard_history.log")

            logging_setup.configure_logging(log_path, console=False, logger=self.logger)
            self.assertFalse(
                any(type(handler) is logging.StreamHandler for handler in self.app_handlers())
            )

            logging_setup.configure_logging(log_path, console=True, logger=self.logger)
            self.assertTrue(
                any(
                    type(handler) is logging.StreamHandler
                    for handler in self.app_handlers()
                )
            )
            logging_setup._remove_app_handlers(self.logger)


if __name__ == "__main__":
    unittest.main()
