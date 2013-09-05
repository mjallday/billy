from __future__ import unicode_literals
import logging

import balanced

from billy.models.processors.base import PaymentProcessor


class BalancedProcessor(PaymentProcessor):

    def __init__(
        self, 
        customer_cls=balanced.Customer, 
        debit_cls=balanced.Debit,
        credit_cls=balanced.Credit,
        logger=None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.customer_cls = customer_cls
        self.debit_cls = debit_cls
        self.credit_cls = credit_cls

    def _to_cent(self, amount):
        cent = amount * 100
        cent = int(cent)
        return cent

    def create_customer(self, customer):
        api_key = customer.company.processor_key
        balanced.configure(api_key)

        self.logger.debug('Creating Balanced customer for %s', customer.guid)
        record = self.customer_cls(**{
            'meta.billy_customer_guid': customer.guid, 
        }).save()
        self.logger.info('Created Balanced customer for %s', customer.guid)
        return record.uri

    def prepare_customer(self, customer, payment_uri=None):
        api_key = customer.company.processor_key
        balanced.configure(api_key)

        self.logger.debug('Preparing customer %s with payment_uri=%s', 
                          customer.guid, payment_uri)
        # when payment_uri is None, it means we are going to use the 
        # default funding instrument, just return
        if payment_uri is None:
            return
        # get balanced customer record
        external_id = customer.external_id
        balanced_customer = self.customer_cls.find(external_id)
        # TODO: use a better way to determine type of URI?
        if '/bank_accounts/' in payment_uri:
            self.logger.debug('Adding bank account %s to %s', 
                              payment_uri, customer.guid)
            balanced_customer.add_bank_account(payment_uri)
            self.logger.info('Added bank account %s to %s', 
                             payment_uri, customer.guid)
        elif '/cards/' in payment_uri:
            self.logger.debug('Adding credit card %s to %s', 
                              payment_uri, customer.guid)
            balanced_customer.add_card(payment_uri)
            self.logger.info('Added credit card %s to %s', 
                             payment_uri, customer.guid)
        else:
            raise ValueError('Invalid payment_uri {}'.format(payment_uri))

    def _do_transaction(
        self, 
        transaction, 
        resource_cls, 
        method_name, 
        extra_kwargs
    ):
        api_key = transaction.subscription.plan.company.processor_key
        balanced.configure(api_key)
        # make sure we won't duplicate debit
        try:
            record = (
                resource_cls.query
                .filter(**{'meta.billy.transaction_guid': transaction.guid})
                .one()
            )
        except balanced.exc.NoResultFound:
            record = None
        # We already have a record there in Balanced, this means we once did
        # transaction, however, we failed to update database. No need to do
        # it again, just return the id
        if record is not None:
            self.logger.warn('Balanced transaction record for %s already '
                             'exist', transaction.guid)
            return record.uri

        # TODO: handle error here
        # get balanced customer record
        external_id = transaction.subscription.customer.external_id
        balanced_customer = self.customer_cls.find(external_id)

        # prepare arguments
        kwargs = dict(
            amount=self._to_cent(transaction.amount),
            description=(
                'Generated by Billy from subscription {}, scheduled_at={}'
                .format(transaction.subscription.guid, transaction.scheduled_at)
            ),
            meta={'billy.transaction_guid': transaction.guid},
        )
        kwargs.update(extra_kwargs)

        method = getattr(balanced_customer, method_name)
        self.logger.debug('Calling %s with args %s', method.__name__, kwargs)
        record = method(**kwargs)
        self.logger.info('Called %s with args %s', method.__name__, kwargs)
        return record.uri

    def charge(self, transaction):
        extra_kwargs = {}
        if transaction.payment_uri is not None:
            extra_kwargs['source_uri'] = transaction.payment_uri
        return self._do_transaction(
            transaction=transaction, 
            resource_cls=self.debit_cls,
            method_name='debit',
            extra_kwargs=extra_kwargs,
        )

    def payout(self, transaction):
        extra_kwargs = {}
        if transaction.payment_uri is not None:
            extra_kwargs['destination_uri'] = transaction.payment_uri
        return self._do_transaction(
            transaction=transaction, 
            resource_cls=self.credit_cls,
            method_name='credit',
            extra_kwargs=extra_kwargs,
        )
