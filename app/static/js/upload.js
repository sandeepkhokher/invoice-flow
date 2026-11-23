// SC-10 Upload. The file input covers the drop zone, so dropping files works without this
// script. It only highlights the zone while dragging, lists the chosen file names, and clears
// the choice after a successful upload. Delegated from the document, so it keeps working after
// htmx (hx-boost) swaps the page.
(function () {
  function zoneOf(node) {
    return node && node.closest ? node.closest("[data-dropzone]") : null;
  }
  function show(zone) {
    var input = zone.querySelector("input[type=file]");
    var list = zone.querySelector("[data-filelist]");
    list.textContent = "";
    Array.prototype.forEach.call(input.files, function (file) {
      var item = document.createElement("li");
      item.textContent = file.name; // text, never HTML
      list.appendChild(item);
    });
  }
  document.addEventListener("change", function (event) {
    var zone = zoneOf(event.target);
    if (zone) show(zone);
  });
  ["dragenter", "dragover"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var zone = zoneOf(event.target);
      if (zone) zone.setAttribute("data-dragover", "");
    });
  });
  ["dragleave", "drop"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var zone = zoneOf(event.target);
      if (zone) zone.removeAttribute("data-dragover");
    });
  });
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt;
    var zone = form && form.querySelector ? form.querySelector("[data-dropzone]") : null;
    if (zone && event.detail.successful) {
      zone.querySelector("input[type=file]").value = "";
      show(zone);
    }
  });
})();
