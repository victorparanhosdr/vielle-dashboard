# Facebook campaign attribution

## Current scope

- Master users can open `crm.html?clinic=vielle&mode=facebook`, synchronize campaigns, and view identified opportunities for the selected doctor.
- The existing Vielle `META_AD_ACCOUNT_ID`, `META_ACCESS_TOKEN` and `META_API_VERSION` settings are reused. No additional OAuth permission is granted by this feature.
- Campaign synchronization and ad lookups are read-only Meta requests. No CRM contacts, phone numbers, clinical records, messages or sales are sent to Meta.
- Form submissions continue to capture configured UTMs independently.

## Direct WhatsApp ads

The Evolution connector must preserve `message.*.contextInfo.externalAdReply`, including a usable `sourceId`. `ctwaClid`, ad title and allowlisted UTMs from `sourceUrl` are retained when present. Quoted or forwarded ad messages, plain URLs, outgoing messages and imported history do not create acquisition evidence.

The worker resolves at most five source IDs per minute through the configured Meta account. The returned ad ID and account must match before campaign and ad-set names are accepted. Lookups are cached for one hour. They run separately from the message-send worker and never hold a database transaction during a network request.

Incoming evidence is attached to the unique opportunity in the channel's destination pipeline, or the contact's only active opportunity for that doctor. Ambiguous receipts are preserved without guessing the opportunity. Repeated webhook delivery does not create another attribution or lead. Identity consolidation and lead transfer preserve attached attribution history through the existing mechanisms.

## Limits and acceptance test

A redirect link by itself cannot identify the phone number of the visitor who later sends a WhatsApp message. Per the chosen product behavior, this implementation does not ask for a phone number, add a reference to the initial message, or introduce an intermediate tracking link.

If the connector omits the native referral, or the source is a post rather than a readable ad in the configured account, the campaign cannot be confirmed. Existing leads without retained referral/UTM evidence cannot be reconstructed from aggregate campaign insights.

After deployment, use an already-approved, active click-to-WhatsApp ad to initiate a real conversation. Verify the received referral, campaign, ad set and ad in the lead profile; then verify the lead in the Facebook tab. Do not activate ads or change spend as part of testing. Synthetic tests do not confirm that the production connector supplies referral metadata.

## Local checks

`tests/test_crm_whatsapp_attribution.py` covers capture, privacy bounds, duplicate delivery, first/last touch, account matching, cache reuse, campaign pagination and scoped campaign views. `tests/test_crm_facebook_ui.cjs` covers the admin interface. `tests/serve_crm_preview.py --attribution-fixture` creates isolated synthetic data with all external sending disabled.
