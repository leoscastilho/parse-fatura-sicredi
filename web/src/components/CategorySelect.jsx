// Um <select> nativo: teclado, busca por digitação e acessibilidade de graça.
// Numa tela com 135 dropdowns, isso importa mais que um combobox bonito.
export default function CategorySelect({ value, categories, onChange, placeholder = '— sem categoria —', id }) {
  const missing = value && !categories.includes(value)
  return (
    <select
      id={id}
      className={`category-select ${value ? '' : 'empty'}`}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder}</option>
      {missing && <option value={value}>{value} (nova)</option>}
      {categories.map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </select>
  )
}
