"""Doble del cliente de Anthropic: registra todo lo que recibiría la API y responde lo que se le programe."""

import json

from anthropic.types import Message


def mensaje(contenido: list[dict], stop_reason="end_turn", tin=1000, tout=200) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": contenido,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": tin,
                "output_tokens": tout,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )


def texto(t: str, **kw) -> Message:
    return mensaje([{"type": "text", "text": t}], **kw)


class _Messages:
    def __init__(self, respuestas):
        self.respuestas, self.recibido = list(respuestas), []

    def create(self, **payload):
        self.recibido.append(payload)
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        return r(payload) if callable(r) else r


class FakeAnthropic:
    def __init__(self, *respuestas):
        self.messages = _Messages(respuestas or [texto("{}")])

    @property
    def todo_lo_enviado(self) -> str:
        """Todo el tráfico saliente como un solo texto (para afirmar que algo NO salió)."""
        return json.dumps(self.messages.recibido, ensure_ascii=False)
