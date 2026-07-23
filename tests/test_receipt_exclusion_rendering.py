from types import SimpleNamespace

import receipt_exclusion_rendering as rendering


class _FakeStreamlit:
    def __init__(self):
        self.checkbox_value = False
        self.checkbox_labels = []
        self.buttons = {}
        self.rendered_text = ""
        self.session_state = {}

    def dataframe(self, value, **kwargs):
        self.rendered_text += value.to_string()

    def checkbox(self, label, **kwargs):
        self.checkbox_labels.append(label)
        return self.checkbox_value

    def multiselect(self, *args, **kwargs):
        return []

    def button(self, label, **kwargs):
        self.buttons[label] = kwargs
        return False

    def markdown(self, value):
        self.rendered_text += value

    def caption(self, value):
        self.rendered_text += value

    def write(self, value):
        self.rendered_text += str(value)


def test_confirmation_requires_checkbox_before_primary_action(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    rendering.render_receipt_exclusion_confirmation(
        {"proposalFingerprint": "proposal-1", "candidates": [{
            "candidateId": "candidate-1", "sourceOrderNo": "31NZY6629115617",
            "receiptNo": "SK2606005393", "exclusionKind": "payment_method:TT 退款轉團款",
            "observedAmount": 1630.0, "affectedRevenue": 1270.0,
        }]},
        confirm_action=lambda payload: payload,
    )
    assert fake.checkbox_labels == [rendering.CONFIRMATION_COPY]
    assert fake.buttons["永久排除並重新預演"]["disabled"] is True


def test_governance_panel_never_exposes_quarantine_payload(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setattr(rendering, "st", fake)
    rendering.render_receipt_exclusion_governance(
        {"active": [{"id": 7, "receiptNo": "SK2606005393"}], "rawPayload": {"secret": "must-not-render"}},
        preview_revoke=lambda rule_id: {}, confirm_revoke=lambda rule_id, fingerprint: {},
    )
    assert "must-not-render" not in fake.rendered_text
