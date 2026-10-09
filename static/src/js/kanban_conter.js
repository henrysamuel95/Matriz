// Función para animar la entrada de las tarjetas Kanban al cargar la página
function animateKanbanCards() {
    const kanbanCards = document.querySelectorAll('.kanban-card');

    // Añadir clase para activar la animación
    kanbanCards.forEach(card => {
        card.style.opacity = '0';
        card.style.transform = 'translateY(-20px)';
    });

    setTimeout(() => {
        kanbanCards.forEach(card => {
            card.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
            card.style.opacity = '1';
            card.style.transform = 'translateY(0)';
        });
    }, 300);

    // Limpiar las transiciones después de la animación
    setTimeout(() => {
        kanbanCards.forEach(card => {
            card.style.transition = '';
        });
    }, 800);
}

// Llamar a la función para animar las tarjetas al cargar la página
document.addEventListener('DOMContentLoaded', () => {
    animateKanbanCards();
});
