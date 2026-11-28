/*
  Keeps text typed into a model row that the reader did not save, and warns
  before that text is lost.

  Each model row is its own form. A Save on one row, or an answer to a
  question, reloads the page and sends only that one form, so text typed into
  another row was lost with no warning. This script does two things:

  1. When a form on the page is sent, it keeps each other row that holds
     unsaved text in sessionStorage. The next render puts that text back in
     its row. The row still holds unsaved text, so the warning still applies.
  2. Before the reader leaves the page any other way -- a link, the rail, a
     reload -- while a row holds unsaved text, the browser asks first.

  If sessionStorage is not available, nothing is kept, so the browser asks
  before a form is sent too. This script reads and writes the model rows
  only. It does not touch the write action or any other control.
*/
(function () {
  "use strict";

  var PREFIX = "dbtw-unsaved:";
  var FIELDS = ["text", "tags", "materialization"];
  var rows = Array.prototype.slice.call(document.querySelectorAll('form[action="/describe"]'));
  var leaving = false;
  var storage = null;
  try {
    storage = window.sessionStorage;
  } catch (refused) {
    storage = null;
  }

  function field(form, name) {
    return form.elements.namedItem(name);
  }

  function initial(element) {
    if (element.tagName !== "SELECT") {
      return element.defaultValue;
    }
    for (var i = 0; i < element.options.length; i += 1) {
      if (element.options[i].defaultSelected) {
        return element.options[i].value;
      }
    }
    return element.options.length ? element.options[0].value : "";
  }

  function dirty(form) {
    return FIELDS.some(function (name) {
      var element = field(form, name);
      return element !== null && element.value !== initial(element);
    });
  }

  function model(form) {
    return field(form, "model").value;
  }

  function keep(form) {
    if (storage === null) {
      return false;
    }
    var values = {};
    FIELDS.forEach(function (name) {
      var element = field(form, name);
      if (element !== null) {
        values[name] = element.value;
      }
    });
    try {
      storage.setItem(PREFIX + model(form), JSON.stringify(values));
      return true;
    } catch (full) {
      return false;
    }
  }

  function restore(form) {
    if (storage === null) {
      return;
    }
    var held = storage.getItem(PREFIX + model(form));
    if (held === null) {
      return;
    }
    storage.removeItem(PREFIX + model(form));
    var values = JSON.parse(held);
    FIELDS.forEach(function (name) {
      var element = field(form, name);
      if (element !== null && Object.prototype.hasOwnProperty.call(values, name)) {
        element.value = values[name];
      }
    });
  }

  rows.forEach(restore);

  document.addEventListener("submit", function (event) {
    var kept = true;
    rows.forEach(function (form) {
      if (form !== event.target && dirty(form)) {
        kept = keep(form) && kept;
      }
    });
    leaving = kept;
  });

  window.addEventListener("beforeunload", function (event) {
    if (leaving || !rows.some(dirty)) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });
})();
