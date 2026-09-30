"""Fill official Ontario forms with invented values, for tagging gold with exact answers.

    uv run python eval/tagging/make_forms.py SRC_DIR

SRC_DIR holds the blank forms as published (Tribunals Ontario, N4.pdf). They're
encrypted with an owner password that permits form filling; pypdf decrypts
with the empty user password, fills the AcroForm fields, drops the XFA layer
(pdfium renders AcroForm) and keeps only the pages that carry values.
Everyone and everything named here is fictional.
"""

import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

OUT = Path(__file__).parent / "docs"
F = "form1[0].sfrmForm[0]."

N4 = {
    F + "InfofromLandlord1[0].txtOweMeAmount[0]": "3,780.00",
    F + "InfofromLandlord1[0].dptPayDate[0]": "15/04/2025",
    F + "PartyAddressInfo[0].txtTenant[0]": "Marcus Oyelaran and Priya Oyelaran",
    F + "PartyAddressInfo[0].txtLandlord[0]": "Lakeshore Rental Holdings Inc.",
    F + "PartyAddressInfo[0].txtNumber[0]": "88",
    F + "PartyAddressInfo[0].txtName[0]": "Elmwood Avenue",
    F + "PartyAddressInfo[0].txtUnit[0]": "1204",
    F + "PartyAddressInfo[0].txtCity[0]": "Mississauga",
    F + "PartyAddressInfo[0].txtProvince[0]": "ON",
    F + "PartyAddressInfo[0].txtPostalCode[0]": "L5B 3X9",
    F + "InfofromLandlord2[0].RentOwing[0].Row1[0].DateFrom1[0]": "01/02/2025",
    F + "InfofromLandlord2[0].RentOwing[0].Row1[0].DateTo1[0]": "28/02/2025",
    F + "InfofromLandlord2[0].RentOwing[0].Row1[0].Charged1[0]": "1,890.00",
    F + "InfofromLandlord2[0].RentOwing[0].Row1[0].Paid1[0]": "0.00",
    F + "InfofromLandlord2[0].RentOwing[0].Row1[0].Owing1[0]": "1,890.00",
    F + "InfofromLandlord2[0].RentOwing[0].Row2[0].DateFrom2[0]": "01/03/2025",
    F + "InfofromLandlord2[0].RentOwing[0].Row2[0].DateTo2[0]": "31/03/2025",
    F + "InfofromLandlord2[0].RentOwing[0].Row2[0].Charged2[0]": "1,890.00",
    F + "InfofromLandlord2[0].RentOwing[0].Row2[0].Paid2[0]": "0.00",
    F + "InfofromLandlord2[0].RentOwing[0].Row2[0].Owing2[0]": "1,890.00",
    F + "InfofromLandlord2[0].TotalRentOwing[0]": "3,780.00",
    F + "Signature[0].txtFirstName[0]": "Daniel",
    F + "Signature[0].txtLastName[0]": "Whitcombe",
    F + "Signature[0].txtPhone1[0]": "(416) 555-0142",
    F + "Signature[0].Signature[0]": "D. Whitcombe",
    F + "Signature[0].dptSignDate[0]": "31/03/2025",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtFirstName[0]": "Daniel",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtLastName[0]": "Whitcombe",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtLSOnumber[0]": "P12345",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtCompanyName[0]": "Whitcombe Legal Services Professional Corporation",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtNumber[0]": "400",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtName[0]": "University Avenue",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtUnit[0]": "1800",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtCity[0]": "Toronto",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtProvince[0]": "ON",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtPostalCode[0]": "M5G 1S5",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtEmail[0]": "dwhitcombe@whitcombelegal.ca",
    F + "LegalRepInfo[0].sfrmLegalRepInfo_1[0].txtPhone1[0]": "(416) 555-0142",
}


def fill(src: Path, values: dict[str, str], dest: Path) -> None:
    reader = PdfReader(src)
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter(clone_from=reader)
    acro = writer._root_object["/AcroForm"]
    if "/XFA" in acro:
        del acro["/XFA"]  # pdfium (no XFA build) renders the AcroForm layer
    for page in writer.pages:
        writer.update_page_form_field_values(page, values)  # also generates appearance streams
    keep = [i for i, page in enumerate(writer.pages)
            if any(a.get_object().get("/T") and a.get_object().get("/V") for a in page.get("/Annots", []) or [])]
    for i in reversed(range(len(writer.pages))):
        if i not in keep:
            writer.remove_page(i)
    with dest.open("wb") as f:
        writer.write(f)
    print(f"{dest.name}: kept pages {[i + 1 for i in keep]}")


if __name__ == "__main__":
    src = Path(sys.argv[1])
    OUT.mkdir(exist_ok=True)
    fill(src / "n4.pdf", N4, OUT / "n4_filled.pdf")
