// Layout shell behaviour (screens.md §1.1). Kept out of the templates so pages carry no inline
// script or style. Below 768 px the menu button shows and hides the side menu.
document.addEventListener("click", function (event) {
  var button = event.target.closest(".menu-button");
  if (!button) return;
  var open = button.getAttribute("aria-expanded") === "true";
  button.setAttribute("aria-expanded", open ? "false" : "true");
});
