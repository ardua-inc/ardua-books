"""
PDF generation views for invoices.
"""
import io
from mimetypes import guess_type

from pypdf import PdfReader, PdfWriter
import weasyprint

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string

from billing.models import Company, Invoice


def get_invoice_pdf_filename(invoice):
    """Generate the PDF filename using company name if available."""
    company = Company.get_instance()
    company_name = company.name if company else "Invoice"
    return f"{company_name} - Invoice {invoice.invoice_number}.pdf"


@login_required
def invoice_print_view(request, pk):
    """Render printable HTML invoice."""
    invoice = get_object_or_404(Invoice, pk=pk)
    company = Company.get_instance()
    return render(request, "billing/invoice_print.html", {
        "invoice": invoice,
        "company": company,
        "embed_receipts": True,
    })


def _generate_invoice_pdf(invoice, request):
    """
    Generate PDF bytes for an invoice with attached receipts.
    Returns the PDF as bytes.
    """
    company = Company.get_instance()

    # Render invoice HTML without embedded receipts
    html = render_to_string(
        "billing/invoice_print.html",
        {
            "invoice": invoice,
            "company": company,
            "embed_receipts": False,
        },
        request=request,
    )

    # Convert invoice HTML to PDF
    base_pdf_bytes = weasyprint.HTML(
        string=html,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    writer = PdfWriter()

    # Add all pages of the invoice PDF
    base_reader = PdfReader(io.BytesIO(base_pdf_bytes))
    for page in base_reader.pages:
        writer.add_page(page)

    # Append each PDF receipt as full pages
    for line in invoice.lines.all():
        expense = getattr(line, "expense", None)
        if not expense or not expense.receipt:
            continue

        path = expense.receipt.path
        mime, _ = guess_type(path)

        # Case 1: PDF receipt - append pages directly
        if mime == "application/pdf":
            receipt_reader = PdfReader(path)
            for page in receipt_reader.pages:
                writer.add_page(page)

        # Case 2: Image receipt - render HTML page with image, convert to PDF
        elif mime and mime.startswith("image/"):
            img_html = render_to_string(
                "billing/receipt_image_page.html",
                {"expense": expense},
                request=request,
            )
            img_pdf_bytes = weasyprint.HTML(
                string=img_html,
                base_url=request.build_absolute_uri("/"),
            ).write_pdf()

            img_reader = PdfReader(io.BytesIO(img_pdf_bytes))
            for page in img_reader.pages:
                writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return output.getvalue()


@login_required
def invoice_print_pdf(request, pk):
    """Generate PDF invoice with attached receipts."""
    invoice = get_object_or_404(Invoice, pk=pk)

    pdf_bytes = _generate_invoice_pdf(invoice, request)
    filename = get_invoice_pdf_filename(invoice)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
