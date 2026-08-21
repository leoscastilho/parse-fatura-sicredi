/**
 * Seletor de banco. Fica na barra de cima porque a escolha vale para a sessão
 * inteira: muda o tema E o perfil de leitura usado no upload.
 */
export default function BankPicker({ banks, value, onChange, disabled }) {
  const atual = banks.find((b) => b.id === value)
  return (
    <div className="bank-picker">
      <label className="muted small" htmlFor="banco">Banco</label>
      <select
        id="banco"
        value={value || ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {banks.map((b) => (
          <option key={b.id} value={b.id}>
            {b.nome}{b.validado ? '' : ' (placeholder)'}
          </option>
        ))}
      </select>
      {atual && !atual.validado && (
        <span className="tag flag" title="perfil ainda não conferido contra uma fatura real">
          não validado
        </span>
      )}
    </div>
  )
}
