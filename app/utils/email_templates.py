"""
Email template utilities
Load and render email HTML templates
"""
from pathlib import Path
from typing import Dict, Optional
from config.settings import BASE_DIR


TEMPLATES_DIR = BASE_DIR / 'templates' / 'email'


def load_template(template_name: str) -> str:
    """
    Load an email template HTML file
    
    Args:
        template_name: Name of template file (e.g., 'order_confirmation.html')
    
    Returns:
        Template content as string
    
    Raises:
        FileNotFoundError: If template doesn't exist
    """
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")
    
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


def render_template(template_name: str, context: Dict[str, str]) -> str:
    """
    Render an email template with context variables
    
    Simple template rendering: replaces {{ variable }} with values
    
    Args:
        template_name: Name of template file
        context: Dictionary of variables to replace
    
    Returns:
        Rendered HTML string
    """
    template = load_template(template_name)
    
    # Simple template replacement: {{ variable }} -> value
    for key, value in context.items():
        placeholder = f"{{{{ {key} }}}}"
        template = template.replace(placeholder, str(value))
    
    # Handle conditional blocks {% if variable %}...{% endif %}
    # Simple implementation: remove {% if variable %} and {% endif %} if variable exists
    # This is a basic implementation, could be improved with a proper templating engine
    
    return template


def format_order_items_html(order_lines) -> str:
    """
    Format order lines as HTML table rows
    
    Args:
        order_lines: List of OrderLine objects
    
    Returns:
        HTML string with table rows
    """
    rows = []
    for line in order_lines:
        color_info = f" ({line.color.nom})" if line.color else ""
        personalisation_info = f" - ✏️ {line.personnalisation}" if line.personnalisation else ""
        row = f"""
        <tr>
            <td style="padding: 10px;">{line.product.nom}{color_info}{personalisation_info}</td>
            <td style="padding: 10px; text-align: center;">{line.quantite}</td>
            <td style="padding: 10px; text-align: right;">{line.prix_vente_total:.0f} ₪</td>
        </tr>
        """
        rows.append(row)
    
    return "".join(rows)

