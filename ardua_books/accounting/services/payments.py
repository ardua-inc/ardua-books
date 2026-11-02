"""
Applying a payment's unapplied balance to an invoice after the payment was
recorded.

A payment's JE credits AR for the amount applied to invoices and 2200
Unapplied Payments for the rest. Applying some of the rest later moves it:

    DR 2200 Unapplied Payments / CR 1100 Accounts Receivable
"""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from accounting import gl_codes
from accounting.models import JournalEntry, Payment, PaymentApplication
from accounting.services.banking import BankTransactionService
from billing.models import Invoice, InvoiceStatus


class PaymentService:

    @staticmethod
    def invoices_open_to(payment):
        """The client's invoices with a balance still outstanding. A PAID
        invoice can still have one (a QuickBooks import marked some paid)."""
        invoices = Invoice.objects.filter(
            client=payment.client, status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PAID]
        ).order_by("issue_date", "id")
        return [inv for inv in invoices if inv.outstanding_balance() > 0]

    @staticmethod
    @transaction.atomic
    def apply_unapplied(payment, invoice, amount, posted_on, user=None):
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        amount = Decimal(amount)

        if amount <= 0:
            raise ValueError("The amount to apply must be positive.")
        if invoice.client_id != payment.client_id:
            raise ValueError("The invoice belongs to a different client from the payment.")
        if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PAID):
            raise ValueError(f"Invoice {invoice.invoice_number} is {invoice.get_status_display()}.")
        if amount > payment.unapplied_amount:
            raise ValueError(f"Only ${payment.unapplied_amount} of the payment is unapplied.")
        outstanding = invoice.outstanding_balance()
        if amount > outstanding:
            raise ValueError(f"Invoice {invoice.invoice_number} has only ${outstanding} outstanding.")

        application, _ = PaymentApplication.objects.get_or_create(
            payment=payment, invoice=invoice, defaults={"amount": Decimal("0")}
        )
        application.amount += amount
        application.save(update_fields=["amount"])

        payment.unapplied_amount -= amount
        payment.save(update_fields=["unapplied_amount"])

        # A payment not yet posted is posted later from its applications
        # (Payment.post_to_accounting), so it needs nothing here.
        payment_posted = JournalEntry.objects.filter(
            source_content_type=ContentType.objects.get_for_model(Payment),
            source_object_id=payment.id,
        ).exists()
        if payment_posted:
            je = JournalEntry.objects.create(
                posted_at=posted_on,
                posted_by=user,
                description=(
                    f"Apply unapplied payment {payment.id} ({payment.client.name}) "
                    f"to invoice {invoice.invoice_number}"
                )[:255],
                # Sourced from the application, not the payment: the payment
                # keeps exactly one JE of its own.
                source_content_type=ContentType.objects.get_for_model(PaymentApplication),
                source_object_id=application.id,
            )
            BankTransactionService.create_lines(je, [
                (gl_codes.gl_account(gl_codes.UNAPPLIED_PAYMENTS), amount, Decimal("0")),
                (gl_codes.gl_account(gl_codes.ACCOUNTS_RECEIVABLE), Decimal("0"), amount),
            ])

        invoice.update_status()
        return application
