// Progressive enhancement for the field editors: keeps the "present" flag in
// sync, clears options when a field stops being a select, and adds/removes rows.

const FIELD_TYPES = ["text", "number", "boolean", "date", "select", "text[]"];

function labelled(text, control) {
  const label = document.createElement("label");
  label.className = "field";
  const span = document.createElement("span");
  span.className = "field-label";
  span.textContent = text;
  label.append(span, control);
  return label;
}

function textInput(name, attributes = {}) {
  const input = document.createElement("input");
  input.name = name;
  Object.assign(input, attributes);
  return input;
}

function typeSelect() {
  const select = document.createElement("select");
  select.name = "field_type";
  for (const value of FIELD_TYPES) {
    const option = document.createElement("option");
    option.textContent = value;
    select.append(option);
  }
  return select;
}

function buildFieldRow(hasValues) {
  const row = document.createElement("div");
  row.className = "field-row";

  const parts = [
    textInput("field_key", { type: "hidden", value: "" }),
    labelled("Label", textInput("field_label", { required: true, placeholder: "Label" })),
    labelled("Type", typeSelect()),
    labelled("Options", textInput("field_options", { placeholder: "a, b, c" })),
  ];

  if (hasValues) {
    parts.push(
      labelled("Value", textInput("field_value", { placeholder: "Value" })),
      textInput("field_present", { type: "hidden", value: "0" }),
    );
  }

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button remove-row";
  remove.title = "Remove this field";
  remove.textContent = "Remove";
  parts.push(remove);

  row.append(...parts);
  return row;
}

document.addEventListener("input", (event) => {
  if (!event.target.matches("[name=field_value]")) return;
  const present = event.target.closest(".field-row").querySelector("[name=field_present]");
  if (present) present.value = "1";
});

document.addEventListener("change", (event) => {
  if (!event.target.matches("[name=field_type]") || event.target.value === "select") return;
  const options = event.target.closest(".field-row").querySelector("[name=field_options]");
  if (options) options.value = "";
});

document.addEventListener("click", (event) => {
  if (event.target.matches(".remove-row")) {
    event.target.closest(".field-row").remove();
    return;
  }
  if (event.target.id === "add-field") {
    const hasValues = event.target.form.dataset.card === "true";
    document.querySelector("#fields").append(buildFieldRow(hasValues));
  }
});
