import React, { useState } from 'react';
import api from '../api';

export default function ExportButton({ examId, disabled = false }) {
  const [downloading, setDownloading] = useState(false);

  const handleExport = async () => {
    if (!examId || disabled) return;
    try {
      setDownloading(true);
      const url = api.getExportUrl(examId);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `AutoGrade_Results_${examId}.xlsx`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
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
      className={`inline-flex items-center gap-2 px-3 py-1.5 rounded text-xs font-mono font-medium tracking-wide transition-colors ${
        disabled || !examId
          ? 'bg-[#151619] text-[#4d515a] border border-[#202227] cursor-not-allowed'
          : 'bg-[#1c1e23] hover:bg-[#24272e] text-[#d6d9e0] border border-[#2e323b] cursor-pointer'
      }`}
    >
      <span>{downloading ? 'Exporting...' : 'Export Spreadsheet (.xlsx)'}</span>
    </button>
  );
}
