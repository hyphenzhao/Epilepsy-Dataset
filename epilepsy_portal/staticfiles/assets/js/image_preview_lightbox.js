(function () {
  function ensureOverlay() {
    let overlay = document.getElementById('imgPreviewOverlay');
    if (overlay) return overlay;

    // Create overlay markup if not present
    overlay = document.createElement('div');
    overlay.id = 'imgPreviewOverlay';
    overlay.className = 'img-preview-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    overlay.innerHTML = `
      <div class="img-preview-dialog" role="dialog" aria-modal="true" aria-label="Image preview">
        <button id="imgPreviewOverlayClose" class="img-preview-close" type="button" aria-label="Close">×</button>
        <img id="imgPreviewOverlayImg" src="" alt="">
      </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
  }

  function openOverlay(src, altText) {
    const overlay = ensureOverlay();
    const img = overlay.querySelector('#imgPreviewOverlayImg');
    if (!img) return;

    img.src = src;
    img.alt = altText || 'preview';

    overlay.classList.add('is-open');
    document.body.style.overflow = 'hidden';
  }

  function closeOverlay() {
    const overlay = document.getElementById('imgPreviewOverlay');
    if (!overlay) return;

    overlay.classList.remove('is-open');
    const img = overlay.querySelector('#imgPreviewOverlayImg');
    if (img) img.src = '';
    document.body.style.overflow = '';
  }

  function bindOnce() {
    // Close button
    document.addEventListener('click', function (e) {
      if (e.target && e.target.id === 'imgPreviewOverlayClose') {
        e.preventDefault();
        closeOverlay();
      }
    });

    // Click outside dialog closes
    document.addEventListener('click', function (e) {
      const overlay = document.getElementById('imgPreviewOverlay');
      if (!overlay || !overlay.classList.contains('is-open')) return;
      if (e.target === overlay) closeOverlay();
    });

    // ESC closes
    document.addEventListener('keydown', function (e) {
      const overlay = document.getElementById('imgPreviewOverlay');
      if (e.key === 'Escape' && overlay && overlay.classList.contains('is-open')) {
        closeOverlay();
      }
    });

    // Delegated click for dynamically inserted preview images
    document.addEventListener('click', function (e) {
      const img = e.target && e.target.closest ? e.target.closest('img[data-preview-src]') : null;
      if (!img) return;

      const src = img.getAttribute('data-preview-src');
      if (!src) return;

      e.preventDefault();
      openOverlay(src, img.getAttribute('alt') || '');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindOnce);
  } else {
    bindOnce();
  }
})();
