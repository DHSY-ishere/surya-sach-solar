// deploy.ps1 rewrites the SURYA_API line after `sam deploy`. Empty = pages run on built-in sample data.
window.SURYA_API = "https://j1ny7vsvcl.execute-api.ap-south-1.amazonaws.com";
// WhatsApp number of the bot: digits only, with country code. Empty hides the WhatsApp links.
window.SURYA_WA = "15556430503";
window.waLink = function (text) {
  return window.SURYA_WA ? "https://wa.me/" + window.SURYA_WA + "?text=" + encodeURIComponent(text || "hi") : "#";
};
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("a[data-wa]").forEach(function (a) {
    if (!window.SURYA_WA) { a.style.display = "none"; return; }
    a.href = window.waLink(a.getAttribute("data-wa"));
    a.target = "_blank"; a.rel = "noopener";
  });
});
