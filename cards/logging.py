"""Keep OAuth callback codes out of development access logs."""
import logging
import re


class RedactOAuthQuery(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(r"(/auth/google/callback/)\?[^\s\"]*", r"\1?[redacted]", message)
        record.args = ()
        return True
