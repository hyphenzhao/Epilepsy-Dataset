(function () {
  'use strict';

  const allowedTags = ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em',
    'del', 'ul', 'ol', 'li', 'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'pre', 'code', 'hr', 'a', 'sup', 'sub'];

  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function syncChildren(parent, source) {
    const desired = Array.from(source.childNodes);
    desired.forEach((node, index) => {
      const current = parent.childNodes[index];
      if (!current || current.nodeType !== node.nodeType || current.nodeName !== node.nodeName) {
        const replacement = node.cloneNode(true);
        if (current) parent.replaceChild(replacement, current);
        else parent.appendChild(replacement);
        return;
      }
      if (node.nodeType === 3) {
        if (current.nodeValue !== node.nodeValue) current.nodeValue = node.nodeValue;
      } else if (node.nodeType === 1) {
        Array.from(current.attributes).forEach(attribute => {
          if (!node.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
        });
        Array.from(node.attributes).forEach(attribute => {
          if (current.getAttribute(attribute.name) !== attribute.value) {
            current.setAttribute(attribute.name, attribute.value);
          }
        });
        syncChildren(current, node);
      }
    });
    while (parent.childNodes.length > desired.length) parent.lastChild.remove();
  }

  class ReportMarkdownPreview {
    constructor(container, options) {
      this.container = container;
      this.document = container.ownerDocument;
      const config = options || {};
      this.requestFrame = config.requestFrame || window.requestAnimationFrame.bind(window);
      this.cancelFrame = config.cancelFrame || window.cancelAnimationFrame.bind(window);
      this.parser = window.marked && new window.marked.Marked({
        gfm: true, breaks: false, async: false,
        renderer: { html: token => escapeHtml(typeof token === 'string' ? token : token.text) },
      });
      this.content = this.document.createElement('article');
      this.content.className = 'report-document';
      this.notice = this.document.createElement('div');
      this.notice.className = 'report-output-notice';
      this.notice.setAttribute('role', 'status');
      this.notice.hidden = true;
      container.replaceChildren(this.content, this.notice);
      this.epoch = 0;
      this.frame = null;
      this.resolveFinish = null;
      this.reset();
    }

    reset() {
      this.epoch++;
      if (this.frame !== null) this.cancelFrame(this.frame);
      this.frame = null;
      if (this.resolveFinish) this.resolveFinish(false);
      this.resolveFinish = null;
      this.finishPromise = null;
      this.raw = '';
      this.visible = '';
      this.queue = [];
      this.position = 0;
      this.credit = 0;
      this.lastTime = null;
      this.ending = false;
      this.active = false;
      this.lastCompleted = null;
      this.liveText = null;
      this.notice.hidden = true;
      this.notice.textContent = '';
      this.content.replaceChildren();
      this.container.scrollTop = 0;
    }

    append(text) {
      if (!text) return;
      this.raw += text;
      this.queue = this.queue.slice(this.position).concat(Array.from(text));
      this.position = 0;
      this.active = true;
      this.notice.hidden = true;
      this.schedule();
    }

    schedule() {
      if (this.frame !== null) return;
      const epoch = this.epoch;
      this.frame = this.requestFrame(time => {
        if (epoch !== this.epoch) return;
        this.frame = null;
        this.tick(time);
      });
    }

    tick(time) {
      const remaining = this.queue.length - this.position;
      if (!remaining) {
        this.lastTime = null;
        if (this.ending) this.complete();
        return;
      }
      const elapsed = this.lastTime === null ? 16 : Math.min(64, Math.max(0, time - this.lastTime));
      this.lastTime = time;
      let speed = Math.min(1400, 60 + remaining * 0.4);
      if (this.ending) speed = Math.max(speed, Math.min(2200, remaining / 1.1 + 160));
      this.credit += elapsed * speed / 1000;
      const count = Math.min(remaining, Math.floor(this.credit));
      if (count) {
        this.credit -= count;
        const next = this.queue.slice(this.position, this.position + count).join('');
        this.position += count;
        this.visible += next;
        this.render(false);
      }
      if (this.position < this.queue.length) this.schedule();
      else {
        this.queue = [];
        this.position = 0;
        this.lastTime = null;
        if (this.ending) this.complete();
      }
    }

    fragment(markdown) {
      if (this.parser && window.DOMPurify) {
        return window.DOMPurify.sanitize(this.parser.parse(markdown), {
          RETURN_DOM_FRAGMENT: true, ALLOWED_TAGS: allowedTags,
          ALLOWED_ATTR: ['href', 'title', 'colspan', 'rowspan', 'align', 'start'],
          ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
        });
      }
      const fragment = this.document.createDocumentFragment();
      const paragraph = this.document.createElement('p');
      paragraph.className = 'report-plain-fallback';
      paragraph.textContent = markdown;
      fragment.appendChild(paragraph);
      return fragment;
    }

    render(final) {
      const scrollTop = this.container.scrollTop;
      const boundary = final ? this.visible.length : this.visible.lastIndexOf('\n') + 1;
      const completed = this.visible.slice(0, boundary);
      const partial = this.visible.slice(boundary);
      // While typing the same line, change only its text node. Reparse the
      // cumulative Markdown when a line completes, retaining block context.
      if (!final && completed === this.lastCompleted && this.liveText && this.liveText.isConnected) {
        this.liveText.nodeValue = partial;
        this.container.scrollTop = scrollTop;
        return;
      }
      const fragment = this.fragment(completed);
      if (!final && (partial || this.active)) {
        const line = this.document.createElement('p');
        line.className = 'report-live-line';
        line.appendChild(this.document.createTextNode(partial));
        const cursor = this.document.createElement('span');
        cursor.className = 'report-typing-cursor';
        cursor.setAttribute('aria-hidden', 'true');
        line.appendChild(cursor);
        fragment.appendChild(line);
      }
      syncChildren(this.content, fragment);
      this.lastCompleted = final ? null : completed;
      const liveLine = this.content.querySelector('.report-live-line');
      this.liveText = liveLine ? liveLine.firstChild : null;
      this.container.scrollTop = scrollTop;
    }

    finish() {
      if (this.finishPromise) return this.finishPromise;
      this.ending = true;
      this.finishPromise = new Promise(resolve => { this.resolveFinish = resolve; });
      if (this.position >= this.queue.length) this.complete();
      else this.schedule();
      return this.finishPromise;
    }

    complete() {
      this.active = false;
      this.visible = this.raw;
      this.render(true);
      if (this.resolveFinish) this.resolveFinish(true);
      this.resolveFinish = null;
    }

    halt(message, kind) {
      this.epoch++;
      if (this.frame !== null) this.cancelFrame(this.frame);
      this.frame = null;
      this.queue = [];
      this.position = 0;
      this.active = false;
      this.visible = this.raw;
      this.render(true);
      if (this.resolveFinish) this.resolveFinish(false);
      this.resolveFinish = null;
      this.notice.textContent = message || '';
      this.notice.className = 'report-output-notice' + (kind === 'error' ? ' report-output-error' : '');
      this.notice.hidden = !message;
    }

    setMarkdown(markdown) {
      this.reset();
      this.raw = this.visible = markdown || '';
      this.render(true);
    }
  }

  window.ReportMarkdownPreview = ReportMarkdownPreview;
})();
