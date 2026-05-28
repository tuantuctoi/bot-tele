import { useState, useEffect } from 'react'
import { getBalance } from '../api'

export default function Dashboard() {
  const [balance, setBalance] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const fetchBalance = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getBalance()
      if (data.success) setBalance(data.data.balance)
      else setError(data.message || 'Lỗi không xác định')
    } catch {
      setError('Không thể kết nối server')
    }
    setLoading(false)
  }

  useEffect(() => { fetchBalance() }, [])

  const fmt = (n) => n != null ? Number(n).toLocaleString('vi-VN') + 'đ' : '—'

  return (
    <div>
      <div className="section-title">Tài khoản</div>
      <div className="card">
        <div className="card-title">Số dư khả dụng</div>
        <div className="card-value">
          {loading ? <span><span className="spinner" />Đang tải...</span> : fmt(balance)}
        </div>
      </div>
      {error && <div className="error-msg">{error}</div>}
      <button className="btn secondary" onClick={fetchBalance} disabled={loading}>
        {loading ? 'Đang làm mới...' : '↻ Làm mới'}
      </button>
    </div>
  )
}
