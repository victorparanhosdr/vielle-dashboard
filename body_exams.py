"""Readers for the verified InBody120 and HandyMet PDF layouts. No external AI."""

import io
import json
import re
import sys
from datetime import datetime


def parse_pdf(data):
    import pdfplumber

    if not data.startswith(b"%PDF-"):
        raise ValueError("Selecione um arquivo PDF válido.")
    with pdfplumber.open(io.BytesIO(data)) as document:
        if not 1 <= len(document.pages) <= 5:
            raise ValueError("O exame deve ter de 1 a 5 páginas.")
        page = document.pages[0]
        text = page.extract_text() or ""
        if len(text.strip()) < 50:
            raise ValueError("PDF sem texto legível. Use o PDF original do equipamento ou registre as medidas manualmente.")
        fields = {}
        warnings = []

        def number(value):
            return float(value.replace(",", "."))

        def capture(key, pattern):
            match = re.search(pattern, text, re.I | re.S)
            if match:
                fields[key] = number(match.group(1))

        if "InBody120" in text:
            # Labels in this model are graphics; values are text at fixed normalized positions.
            words = page.extract_words()

            def zone(key, x0, x1, y0, y1):
                found = [word["text"] for word in words
                         if x0 <= word["x0"] / page.width <= x1
                         and y0 <= word["top"] / page.height <= y1]
                values = [re.match(r"^(\d+(?:[.,]\d+)?)", item) for item in found]
                values = [item.group(1) for item in values if item]
                if len(values) == 1:
                    fields[key] = number(values[0])

            zones = {
                "water_l": (.43, .49, .137, .152),
                "fat_kg": (.43, .49, .202, .221),
                "weight_kg": (.43, .49, .226, .244),
                "muscle_kg": (.75, .82, .296, .314),
                "fat_free_kg": (.75, .82, .314, .330),
                "bmr_kcal": (.75, .83, .330, .345),
                "waist_hip_ratio": (.75, .82, .345, .362),
                "visceral_level": (.75, .82, .362, .379),
                "bmi": (.15, .57, .471, .486),
                "fat_pct": (.15, .57, .499, .514),
            }
            for key, bounds in zones.items():
                zone(key, *bounds)
            capture("height_cm", r"(\d{2,3})\s*cm")
            stamp = re.search(r"(\d{2}\.\d{2}\.\d{4})\.?\s+(\d{2}:\d{2})", text)
            name = re.search(r"\(([^\n]+)\)", text)
            patient_name = name.group(1) if name else ""
            source, method = "inbody", "InBody120"
            warnings.append("Confira a identidade: o nome pode estar abreviado no InBody. O ID do aparelho não é o ID do Clínica Experts.")
            warnings.append("TMB estimada pela bioimpedância; não substitui o resultado da calorimetria.")
            if not {"weight_kg", "fat_kg", "muscle_kg", "fat_pct"} <= fields.keys():
                raise ValueError("O layout InBody difere do modelo validado. Preencha manualmente e confira o PDF.")
            fmt = "%d.%m.%Y %H:%M"
        elif "handymet.com" in text.lower() and "Calorimetria" in text:
            source, method = "handymet", "HandyMet"
            stamp = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", text)
            patient_name = ""
            if stamp:
                patient_name = text[:stamp.start()].splitlines()[-1].strip()
            capture("height_cm", r"(\d{2,3})\s*cm")
            capture("weight_kg", r"(\d+(?:[.,]\d+)?)\s*kg\b")
            capture("bmi", r"\d{2,3}\s*cm\s+(\d+(?:[.,]\d+)?)")
            capture("bmr_kcal", r"Taxa Metabólica Basal.*?(\d+(?:[.,]\d+)?)\s*KCal/Dia")
            capture("predicted_bmr_kcal", r"TMB Previsto:\s*(\d+(?:[.,]\d+)?)")
            capture("tdee_kcal", r"Gasto energético total.*?(\d+(?:[.,]\d+)?)\s*KCal/Dia")
            match = re.search(r"(\d+[.,]\d+)\s+(\d+[.,]\d+)%\s+(\d+[.,]\d+)%\s+(\d+[.,]\d+)\s*ml/\s*Kg.min\s+(\d+[.,]\d+)\s*l/min", text, re.I)
            if match:
                fields.update(zip(("rq", "fat_fuel_pct", "carb_fuel_pct", "vo2", "ventilation"), map(number, match.groups())))
            warnings.append("O laudo informa TMB estimada a partir de TMR menos 10%. GET e TMB prevista são medidas distintas.")
            fmt = "%d/%m/%Y %H:%M"
            if "bmr_kcal" not in fields:
                raise ValueError("Não foi possível reconhecer os resultados HandyMet com segurança.")
        else:
            raise ValueError("Modelo não reconhecido. Nesta versão: InBody120 e HandyMet com texto. Não foi feita nenhuma gravação.")
        if not stamp:
            raise ValueError("Data do exame não identificada. Registre manualmente após conferir o PDF.")
        date = datetime.strptime(" ".join(stamp.groups()), fmt)
        return {"source": source, "method": method, "exam_at": date.isoformat(timespec="minutes"),
                "patient_name": patient_name, "fields": fields, "warnings": warnings,
                "parser_version": "inspire-1"}


if __name__ == "__main__":
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        result = {"ok": True, "exam": parse_pdf(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))}
    except Exception as exc:
        result = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "Não foi possível ler este PDF."}
    print(json.dumps(result, ensure_ascii=False))
