/**
 * Gestión Ganadera MVP - Scripts Globales
 */

// Auto-ocultar mensajes después de 5 segundos
document.addEventListener('DOMContentLoaded', function() {
  const messages = document.querySelectorAll('.message');
  messages.forEach(function(msg) {
    setTimeout(function() {
      msg.style.opacity = '0';
      msg.style.transition = 'opacity 0.5s';
      setTimeout(function() { msg.remove(); }, 500);
    }, 5000);
  });
});

// Confirmar acciones peligrosas
function confirmAction(message, formId) {
  if (confirm(message)) {
    if (formId) {
      document.getElementById(formId).submit();
    }
    return true;
  }
  return false;
}

// HTMX: mostrar spinner en peticiones
document.addEventListener('htmx:beforeRequest', function(evt) {
  const indicator = evt.target.querySelector('[data-loading]');
  if (indicator) indicator.classList.remove('hidden');
});

document.addEventListener('htmx:afterRequest', function(evt) {
  const indicator = evt.target.querySelector('[data-loading]');
  if (indicator) indicator.classList.add('hidden');
});
