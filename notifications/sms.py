"""
Pluggable SMS backend.

Set SMS_BACKEND in settings.py to switch between backends.
Default: ConsoleSMSBackend (prints to console for development).
"""
import logging
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class SMSBackend:
    """Base class for SMS backends."""

    def send(self, phone, message):
        """Send an SMS. Override in subclasses."""
        raise NotImplementedError


class ConsoleSMSBackend(SMSBackend):
    """
    Development backend — prints SMS to console/log.
    Does not actually send anything.
    """

    def send(self, phone, message):
        logger.info(f'[SMS] To: {phone}')
        logger.info(f'[SMS] Message: {message}')
        print(f'\n{"="*60}')
        print(f'  📱 SMS NOTIFICATION (Console Backend)')
        print(f'{"="*60}')
        print(f'  To:      {phone}')
        print(f'  Message: {message}')
        print(f'{"="*60}\n')
        return True


class MSG91Backend(SMSBackend):
    """
    Production backend for MSG91.
    Requires SMS_API_KEY and SMS_SENDER_ID in settings.

    TODO: Implement when SMS provider is chosen.
    """

    def send(self, phone, message):
        api_key = getattr(settings, 'SMS_API_KEY', '')
        sender_id = getattr(settings, 'SMS_SENDER_ID', '')

        if not api_key:
            logger.error('MSG91 API key not configured')
            return False

        # Placeholder for actual API call
        # import requests
        # response = requests.post(
        #     'https://api.msg91.com/api/v5/flow/',
        #     headers={'authkey': api_key},
        #     json={...}
        # )

        logger.warning(
            f'MSG91Backend.send() called but not fully implemented. '
            f'Phone: {phone}, Message: {message[:50]}...'
        )
        return False


def get_sms_backend():
    """
    Get the configured SMS backend instance.
    Uses SMS_BACKEND setting, defaults to ConsoleSMSBackend.
    """
    backend_path = getattr(settings, 'SMS_BACKEND', 'notifications.sms.ConsoleSMSBackend')
    try:
        backend_class = import_string(backend_path)
        return backend_class()
    except ImportError:
        logger.error(f'Could not import SMS backend: {backend_path}')
        return ConsoleSMSBackend()
