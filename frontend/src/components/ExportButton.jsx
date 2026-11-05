import React, { useState } from 'react';
import api from '../api';

function saveFile(response, fallbackName) {
  const disposition = response.headers['content-disposition'] || '';
  const match = disposition.match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(response.data);
  const link = document.createElement('a');
  link.href = url;
  link.download = match ? match[1] : fallbackName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export default function ExportButton({ examId, disabled = false }) {
  const [downloading, setDownloading] = useState(false);

  const handleExport = async () => {
    if (!examId || disabled) return;
    const fallback = `AutoGrade_Results_${examId}.xlsx`;
    try {
      setDownloading(true);
      try {
        saveFile(await api.downloadExport(examId), fallback);
      } catch (err) {
        if (err.response?.status !== 409) throw err;
        const detail = JSON.parse(await err.response.data.text()).detail;
        if (window.confirm(`${detail.message}\n\nDownload the copy marked NOT FINAL?`)) {
          saveFile(await api.downloadExport(examId, true), fallback);
        }
      }
    } catch (err) {
      console.error('Export download error:', err);
      alert('Failed to download export. Ensure results exist for this exam.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <button
      onClick={handleExport}
      disabled={disabled || downloading || !examId}
      className={`inline-flex items-center gap-2 px-3 py-1.5 rounded text-xs font-mono font-medium transition-colors ${
        disabled || !examId
          ? 'bg-[#151619] text-[#4d515a] border border-[#202227] cursor-not-allowed'
          : 'bg-[#1c1e23] hover:bg-[#24272e] text-[#d6d9e0] border border-[#2e323b] cursor-pointer'
      }`}
    >
      <span>{downloading ? 'Exporting...' : 'Export Spreadsheet (.xlsx)'}</span>
    </button>
  );
}
