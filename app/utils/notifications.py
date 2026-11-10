"""
Notification utilities
Email/SMS notifications using Brevo API and BulkGate (for Israel)
"""
import logging
import requests
import re
from typing import List, Optional
from datetime import datetime
from config.settings import (
    BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME, BREVO_ENABLED,
    BULKGATE_APPLICATION_ID, BULKGATE_APPLICATION_TOKEN, BULKGATE_ENABLED,
    TELEGRAM_BOT_TOKEN, TELEGRAM_ENABLED,
)

# Setup logging
logger = logging.getLogger(__name__)

# Brevo API endpoints
BREVO_EMAIL_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_SMS_API_URL = "https://api.brevo.com/v3/transactionalSMS/sms"

# BulkGate API endpoint
BULKGATE_SMS_API_URL = "https://portal.bulkgate.com/api/1.0/simple/transactional"

# Telegram API endpoint
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def detect_country_code(phone: str) -> Optional[str]:
    """
    Detect country code from phone number
    
    Args:
        phone: Phone number with country code (e.g. "+972545551234")
    
    Returns:
        Country code (e.g. "972" for Israel) or None if invalid
    """
    if not phone or not phone.startswith('+'):
        return None
    
    # Extract country code (1-3 digits after +)
    match = re.match(r'^\+(\d{1,3})', phone)
    if match:
        return match.group(1)
    
    return None


def send_sms_via_bulkgate(
    to: str,
    message: str,
    sender: str = "Malitan"
) -> bool:
    """
    Send SMS notification using BulkGate API (cheaper for Israel)
    
    Args:
        to: Phone number with country code (e.g. "+972545551234")
        message: SMS message
        sender: Sender name (max 11 chars, alphanumeric only)
    
    Returns:
        True if sent successfully, False otherwise
    
    Note:
        - BulkGate is optimized for Israel (972) - much cheaper than Brevo
        - Phone numbers must include country code with + prefix
        - Messages over 160 chars count as multiple SMS
    """
    # Check if BulkGate is enabled
    if not BULKGATE_ENABLED:
        logger.warning("BulkGate is not enabled (no credentials). SMS not sent.")
        logger.info(f"[SMS BulkGate] To: {to}, Message: {message}")
        return False
    
    # Validate phone number
    if not to:
        logger.error("Empty phone number")
        return False
    
    # Ensure phone starts with +
    phone = to.strip()
    if not phone.startswith('+'):
        logger.error(f"Invalid phone number (must start with +): {phone}")
        return False
    
    # Validate message
    if not message:
        logger.error("Empty SMS message")
        return False
    
    # Truncate sender to 11 chars
    sender = sender[:11]
    
    try:
        # Prepare payload for BulkGate API
        # Documentation: https://help.bulkgate.com/docs/en/http-rest-api.html
        payload = {
            "application_id": BULKGATE_APPLICATION_ID,
            "application_token": BULKGATE_APPLICATION_TOKEN,
            "number": phone,
            "text": message,
            "sender_id": sender,
            "sender_id_value": sender,
            "unicode": True  # Support Hebrew characters
        }
        
        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Send request
        response = requests.post(BULKGATE_SMS_API_URL, json=payload, headers=headers, timeout=10)
        
        # Check response
        if response.status_code == 200:
            response_data = response.json()
            # BulkGate returns status in response
            if response_data.get('status') == 'success' or response_data.get('data', {}).get('status') == 'sent':
                logger.info(f"SMS sent successfully via BulkGate to {phone}")
                return True
            else:
                logger.error(f"BulkGate API returned error: {response_data}")
                return False
        else:
            logger.error(f"Failed to send SMS via BulkGate to {phone}. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending SMS via BulkGate to {phone}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending SMS via BulkGate to {phone}: {str(e)}")
        return False


def send_email_notification(
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    attachments: Optional[List[dict]] = None
) -> bool:
    """
    Send email notification using Brevo API
    
    Args:
        to: Recipient email
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
        attachments: Optional list of attachments, each dict should have:
            - 'name': filename (e.g., 'invoice.pdf')
            - 'content': base64 encoded file content
    
    Returns:
        True if sent successfully, False otherwise
    """
    import base64
    
    # Check if Brevo is enabled
    if not BREVO_ENABLED:
        logger.warning("Brevo is not enabled (no API key). Email not sent.")
        logger.info(f"[EMAIL] To: {to}, Subject: {subject}")
        return False
    
    # Validate email
    if not to or '@' not in to:
        logger.error(f"Invalid email address: {to}")
        return False
    
    try:
        # Prepare payload
        payload = {
            "sender": {
                "name": BREVO_SENDER_NAME,
                "email": BREVO_SENDER_EMAIL
            },
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body
        }
        
        # Add HTML content if provided
        if html_body:
            payload["htmlContent"] = html_body
        
        # Add attachments if provided
        if attachments:
            payload["attachment"] = attachments
        
        # Prepare headers
        headers = {
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        }
        
        # Send request
        response = requests.post(BREVO_EMAIL_API_URL, json=payload, headers=headers, timeout=10)
        
        # Check response
        if response.status_code == 201:
            logger.info(f"Email sent successfully to {to}: {subject}")
            return True
        else:
            logger.error(f"Failed to send email to {to}. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending email to {to}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email to {to}: {str(e)}")
        return False


def send_sms_notification(
    to: str,
    message: str,
    sender: str = "Malitan"
) -> bool:
    """
    Send SMS notification using BulkGate API (cheaper alternative to Brevo)
    
    Args:
        to: Phone number with country code (e.g. "+972545551234" or "+33612345678")
        message: SMS message (max 160 chars = 1 credit)
        sender: Sender name (max 11 chars, alphanumeric only)
    
    Returns:
        True if sent successfully, False otherwise
    
    Note:
        - Uses BulkGate for all SMS (much cheaper than Brevo)
        - Phone numbers must include country code with + prefix
        - Messages over 160 chars count as multiple SMS
        - Brevo is NOT used for SMS (only for emails)
    """
    # Validate phone number
    if not to:
        logger.error("Empty phone number")
        return False
    
    # Ensure phone starts with +
    phone = to.strip()
    if not phone.startswith('+'):
        logger.error(f"Invalid phone number (must start with +): {phone}")
        return False
    
    # Validate message
    if not message:
        logger.error("Empty SMS message")
        return False
    
    # Use BulkGate for all SMS
    if BULKGATE_ENABLED:
        country_code = detect_country_code(phone)
        logger.info(f"Using BulkGate for SMS to +{country_code}")
        return send_sms_via_bulkgate(phone, message, sender)
    
    # No provider available
    logger.warning("BulkGate is not enabled. SMS not sent.")
    logger.info(f"[SMS] To: {phone}, Message: {message}")
    return False


def send_sms_via_brevo(
    to: str,
    message: str,
    sender: str = "Malitan"
) -> bool:
    """
    Send SMS notification using Brevo API
    
    Args:
        to: Phone number with country code (e.g. "+972545551234" or "+33612345678")
        message: SMS message (max 160 chars = 1 credit)
        sender: Sender name (max 11 chars, alphanumeric only)
    
    Returns:
        True if sent successfully, False otherwise
    
    Note:
        - SMS credits must be purchased from Brevo (not included in free plan)
        - Phone numbers must include country code with + prefix
        - Cost per SMS varies by destination country
        - Messages over 160 chars count as multiple SMS
    """
    # Check if Brevo is enabled
    if not BREVO_ENABLED:
        logger.warning("Brevo is not enabled (no API key). SMS not sent.")
        logger.info(f"[SMS Brevo] To: {to}, Message: {message}")
        return False
    
    # Validate phone number
    if not to:
        logger.error("Empty phone number")
        return False
    
    # Ensure phone starts with +
    phone = to.strip()
    if not phone.startswith('+'):
        logger.error(f"Invalid phone number (must start with +): {phone}")
        return False
    
    # Validate message
    if not message:
        logger.error("Empty SMS message")
        return False
    
    # Truncate sender to 11 chars (Brevo limit)
    sender = sender[:11]
    
    try:
        # Prepare payload
        payload = {
            "sender": sender,
            "recipient": phone,
            "content": message,
            "type": "transactional"
        }
        
        # Prepare headers
        headers = {
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        }
        
        # Send request
        response = requests.post(BREVO_SMS_API_URL, json=payload, headers=headers, timeout=10)
        
        # Check response
        if response.status_code == 201:
            logger.info(f"SMS sent successfully via Brevo to {phone}")
            return True
        else:
            logger.error(f"Failed to send SMS via Brevo to {phone}. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending SMS via Brevo to {phone}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending SMS via Brevo to {phone}: {str(e)}")
        return False


def notify_order_status_change(
    order_numero: str,
    old_status: str,
    new_status: str,
    client_email: Optional[str] = None
) -> bool:
    """
    Send notification when order status changes
    
    Args:
        order_numero: Order number
        old_status: Previous status
        new_status: New status
        client_email: Optional client email
    
    Returns:
        True if notification sent
    """
    subject = f"Mise à jour de votre commande {order_numero}"
    body = f"""
Bonjour,

Votre commande {order_numero} a été mise à jour.

Statut précédent : {old_status}
Nouveau statut : {new_status}

Merci de votre confiance !

L'équipe Malitan 3D
    """
    
    if client_email:
        return send_email_notification(client_email, subject, body)
    
    return False


def notify_low_stock(color_name: str, stock_grammes: float, recipients: List[str]) -> bool:
    """
    Send low stock alert with HTML template
    
    Args:
        color_name: Color name
        stock_grammes: Current stock in grams
        recipients: List of recipient emails
    
    Returns:
        True if all notifications sent
    """
    from utils.email_templates import render_template
    
    stock_kg = round(stock_grammes / 1000, 2)
    
    # Try to use HTML template
    try:
        html_body = render_template('low_stock_alert.html', {
            'color_name': color_name,
            'stock_grams': f"{stock_grammes:.0f}",
            'stock_kg': f"{stock_kg}"
        })
    except Exception as e:
        logger.warning(f"Could not load template, using plain text: {e}")
        html_body = None
    
    subject = f"⚠️ Alerte stock bas : {color_name}"
    body = f"""
Attention,

Le stock de filament {color_name} est bas : {stock_grammes:.0f}g ({stock_kg} kg) restants.

Pensez à commander du filament !

Malitan 3D Manager
    """
    
    results = []
    for recipient in recipients:
        results.append(send_email_notification(recipient, subject, body, html_body))
    
    return all(results)


def notify_overdue_delivery(order_numero: str, client_name: str, recipients: List[str], expected_date: Optional[str] = None, days_late: Optional[int] = None) -> bool:
    """
    Send overdue delivery alert with HTML template
    
    Args:
        order_numero: Order number
        client_name: Client name
        recipients: List of recipient emails
        expected_date: Expected delivery date (optional)
        days_late: Number of days late (optional)
    
    Returns:
        True if all notifications sent
    """
    from utils.email_templates import render_template
    
    # Try to use HTML template
    try:
        context = {
            'order_number': order_numero,
            'client_name': client_name,
            'expected_date': expected_date or 'Non spécifiée',
            'days_late': str(days_late) if days_late else 'Non calculé'
        }
        html_body = render_template('overdue_order.html', context)
    except Exception as e:
        logger.warning(f"Could not load template, using plain text: {e}")
        html_body = None
    
    subject = f"⚠️ Livraison en retard : {order_numero}"
    body = f"""
Attention,

La commande {order_numero} pour {client_name} est en retard.
{f'Date prévue : {expected_date}' if expected_date else ''}
{f'Retard : {days_late} jour(s)' if days_late else ''}

Vérifiez l'état de la commande.

Malitan 3D Manager
    """
    
    results = []
    for recipient in recipients:
        results.append(send_email_notification(recipient, subject, body, html_body))
    
    return all(results)


# ============================================================================
# SMS NOTIFICATIONS FOR ORDERS
# ============================================================================

def notify_order_confirmation_sms(order, client_phone: Optional[str] = None) -> bool:
    """
    Send order confirmation SMS to client
    
    Args:
        order: Order object
        client_phone: Client phone (optional, will use order.client.telephone if not provided)
    
    Returns:
        True if SMS sent successfully
    """
    if not client_phone:
        client_phone = order.client.telephone if order.client else None
    
    if not client_phone:
        logger.warning(f"No phone for order {order.numero_commande}")
        return False
    
    # Keep SMS short (max 160 chars for 1 credit)
    client_name = order.client.full_name if order.client else 'Client'
    message = f"Malitan Studio: Bonjour {client_name}, commande {order.numero_commande} confirmée. Total: {order.total_prix_vente:.0f}₪. Merci!"
    
    return send_sms_notification(client_phone, message)


def notify_order_ready_sms(order, client_phone: Optional[str] = None) -> bool:
    """
    Send SMS when order is ready for delivery
    
    Args:
        order: Order object
        client_phone: Client phone (optional)
    
    Returns:
        True if SMS sent successfully
    """
    if not client_phone:
        client_phone = order.client.telephone if order.client else None
    
    if not client_phone:
        logger.warning(f"No phone for order {order.numero_commande}")
        return False
    
    client_name = order.client.full_name if order.client else 'Client'
    message = f"Malitan Studio: Bonjour {client_name}, votre commande {order.numero_commande} est prête! Nous vous contacterons pour la livraison."
    
    return send_sms_notification(client_phone, message)


def notify_order_shipped_sms(order, tracking_number: Optional[str] = None, client_phone: Optional[str] = None) -> bool:
    """
    Send SMS when order is shipped
    
    Args:
        order: Order object
        tracking_number: Optional tracking number
        client_phone: Client phone (optional)
    
    Returns:
        True if SMS sent successfully
    """
    if not client_phone:
        client_phone = order.client.telephone if order.client else None
    
    if not client_phone:
        logger.warning(f"No phone for order {order.numero_commande}")
        return False
    
    client_name = order.client.full_name if order.client else 'Client'
    
    if tracking_number:
        message = f"Malitan Studio: {client_name}, commande {order.numero_commande} expédiée. Suivi: {tracking_number}"
    else:
        message = f"Malitan Studio: {client_name}, votre commande {order.numero_commande} a été expédiée. Merci!"
    
    return send_sms_notification(client_phone, message)


# ============================================================================
# SMART NOTIFICATION FUNCTIONS (Email with SMS fallback)
# ============================================================================

def send_order_notification(
    order,
    notification_type: str,
    tracking_number: Optional[str] = None,
    tracking_url: Optional[str] = None
) -> dict:
    """
    Send order notification via email, fallback to SMS if no email available
    
    Args:
        order: Order object
        notification_type: Type of notification ("confirmation", "ready", "shipped")
        tracking_number: Optional tracking number (for shipped notifications)
        tracking_url: Optional tracking URL (for shipped notifications)
    
    Returns:
        Dictionary with keys: 'success', 'method' (email/sms/none), 'message'
    """
    client = order.client
    
    if not client:
        return {
            'success': False,
            'method': 'none',
            'message': 'No client associated with order'
        }
    
    # Try email first (preferred)
    if client.email and '@' in client.email:
        try:
            if notification_type == "confirmation":
                success = notify_order_confirmation(order, client.email)
            elif notification_type == "ready":
                success = notify_order_ready(order, client.email)
            elif notification_type == "shipped":
                success = False  # Shipped emails were removed with invoices; fall back to SMS
            else:
                return {
                    'success': False,
                    'method': 'none',
                    'message': f'Unknown notification type: {notification_type}'
                }
            
            if success:
                return {
                    'success': True,
                    'method': 'email',
                    'message': f'Email sent to {client.email}'
                }
        except Exception as e:
            logger.warning(f"Email failed for order {order.numero_commande}, trying SMS: {e}")
    
    # Fallback to SMS if email failed or not available
    if client.telephone and client.telephone.strip():
        try:
            if notification_type == "confirmation":
                success = notify_order_confirmation_sms(order, client.telephone)
            elif notification_type == "ready":
                success = notify_order_ready_sms(order, client.telephone)
            elif notification_type == "shipped":
                success = notify_order_shipped_sms(order, tracking_number, client.telephone)
            else:
                return {
                    'success': False,
                    'method': 'none',
                    'message': f'Unknown notification type: {notification_type}'
                }
            
            if success:
                return {
                    'success': True,
                    'method': 'sms',
                    'message': f'SMS sent to {client.telephone}'
                }
            else:
                return {
                    'success': False,
                    'method': 'sms',
                    'message': f'Failed to send SMS to {client.telephone}'
                }
        except Exception as e:
            logger.error(f"SMS failed for order {order.numero_commande}: {e}")
            return {
                'success': False,
                'method': 'sms',
                'message': f'SMS error: {str(e)}'
            }
    
    # No contact method available
    return {
        'success': False,
        'method': 'none',
        'message': f'No email or phone available for client {client.full_name}'
    }


# ============================================================================
# EMAIL NOTIFICATIONS FOR ORDERS
# ============================================================================

def notify_order_confirmation(order, client_email: Optional[str] = None) -> bool:
    """
    Send order confirmation email to client
    
    Args:
        order: Order object
        client_email: Client email (optional, will use order.client.email if not provided)
    
    Returns:
        True if email sent successfully
    """
    from utils.email_templates import render_template, format_order_items_html
    
    if not client_email:
        client_email = order.client.email if order.client else None
    
    if not client_email:
        logger.warning(f"No email for order {order.numero_commande}")
        return False
    
    try:
        # Format order items
        items_html = format_order_items_html(order.order_lines)
        
        # Prepare context
        context = {
            'client_name': order.client.full_name if order.client else 'Client',
            'order_number': order.numero_commande,
            'order_date': order.date_commande.strftime('%d/%m/%Y'),
            'total_price': f"{order.total_prix_vente:.0f}",
            'order_items': items_html,
            'delivery_date': order.date_livraison_prevue.strftime('%d/%m/%Y') if order.date_livraison_prevue else ''
        }
        
        # Render template
        html_body = render_template('order_confirmation.html', context)
        
        # Plain text fallback
        body = f"""
Bonjour {context['client_name']},

Nous avons bien reçu votre commande {order.numero_commande} du {context['order_date']}.

Total : {context['total_price']} ₪

Merci de votre confiance !

L'équipe Malitan 3D
        """
        
        subject = f"Confirmation de commande {order.numero_commande}"
        
        return send_email_notification(client_email, subject, body, html_body)
        
    except Exception as e:
        logger.error(f"Error sending order confirmation for {order.numero_commande}: {e}")
        return False


def notify_order_ready(order, client_email: Optional[str] = None) -> bool:
    """
    Send email when order is ready for delivery
    
    Args:
        order: Order object
        client_email: Client email (optional)
    
    Returns:
        True if email sent successfully
    """
    from utils.email_templates import render_template
    
    if not client_email:
        client_email = order.client.email if order.client else None
    
    if not client_email:
        logger.warning(f"No email for order {order.numero_commande}")
        return False
    
    try:
        context = {
            'client_name': order.client.full_name if order.client else 'Client',
            'order_number': order.numero_commande,
            'delivery_mode': order.mode_livraison.value if order.mode_livraison else 'livraison'
        }
        
        html_body = render_template('order_ready.html', context)
        
        body = f"""
Bonjour {context['client_name']},

Votre commande {order.numero_commande} est maintenant prête à être livrée.

Nous vous contacterons prochainement pour organiser la livraison.

Merci de votre patience !

L'équipe Malitan 3D
        """
        
        subject = f"Votre commande {order.numero_commande} est prête !"
        
        return send_email_notification(client_email, subject, body, html_body)
        
    except Exception as e:
        logger.error(f"Error sending order ready notification for {order.numero_commande}: {e}")
        return False


# ---------------------------------------------------------------------------
# Telegram — notifications internes David / Lea
# ---------------------------------------------------------------------------

def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a Telegram message to a specific chat_id via the bot API."""
    if not TELEGRAM_ENABLED or not chat_id:
        return False
    try:
        url = TELEGRAM_API_URL.format(token=TELEGRAM_BOT_TOKEN)
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Telegram message sent to chat_id={chat_id}")
            return True
        logger.warning(f"Telegram API {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def _get_telegram_chat_id(db, role: str) -> Optional[str]:
    """Return the telegram_chat_id stored for the user with the given role value."""
    from database.models import User, UserRole
    user = db.query(User).filter(User.role == UserRole(role)).first()
    return (user.telegram_chat_id or None) if user else None


def notify_telegram_new_order(order, db) -> None:
    """Notify David when a new order is created."""
    if not TELEGRAM_ENABLED:
        return
    try:
        chat_id = _get_telegram_chat_id(db, 'david')
        if not chat_id:
            return
        client = order.client
        client_name = f"{client.prenom or ''} {client.nom}".strip() if client else "—"
        nb = len(order.order_lines)
        send_telegram_message(
            chat_id,
            f"🆕 <b>Nouvelle commande #{order.id}</b>\n"
            f"Client : {client_name}\n"
            f"{nb} article(s)",
        )
    except Exception as e:
        logger.warning(f"notify_telegram_new_order failed: {e}")


def notify_telegram_order_paid(order, db) -> None:
    """Notify David when an order is paid (PAYE)."""
    if not TELEGRAM_ENABLED:
        return
    try:
        chat_id = _get_telegram_chat_id(db, 'david')
        if not chat_id:
            return
        client = order.client
        client_name = f"{client.prenom or ''} {client.nom}".strip() if client else "—"
        send_telegram_message(
            chat_id,
            f"💰 <b>Commande #{order.id} payée</b>\n"
            f"Client : {client_name}\n"
            f"À imprimer !",
        )
    except Exception as e:
        logger.warning(f"notify_telegram_order_paid failed: {e}")


def notify_telegram_livre_lea(order_line, db) -> None:
    """Notify Lea when David delivers a printed line to her (LIVRE_LEA)."""
    if not TELEGRAM_ENABLED:
        return
    try:
        chat_id = _get_telegram_chat_id(db, 'lea')
        if not chat_id:
            return
        order = order_line.order
        client = order.client if order else None
        client_name = f"{client.prenom or ''} {client.nom}".strip() if client else "—"
        product_name = order_line.product.nom if order_line.product else "—"
        color_name = order_line.color.nom if order_line.color else "—"
        send_telegram_message(
            chat_id,
            f"📦 <b>Dépôt de David — Commande #{order.id if order else '?'}</b>\n"
            f"Client : {client_name}\n"
            f"{product_name} ({color_name}) × {order_line.quantite}",
        )
    except Exception as e:
        logger.warning(f"notify_telegram_livre_lea failed: {e}")


def notify_telegram_pret_livraison(order, db) -> None:
    """Notify Lea when an order is ready for delivery (PRET_LIVRAISON)."""
    if not TELEGRAM_ENABLED:
        return
    try:
        chat_id = _get_telegram_chat_id(db, 'lea')
        if not chat_id:
            return
        client = order.client
        client_name = f"{client.prenom or ''} {client.nom}".strip() if client else "—"
        send_telegram_message(
            chat_id,
            f"🚚 <b>Commande #{order.id} prête à livrer</b>\n"
            f"Client : {client_name}",
        )
    except Exception as e:
        logger.warning(f"notify_telegram_pret_livraison failed: {e}")

