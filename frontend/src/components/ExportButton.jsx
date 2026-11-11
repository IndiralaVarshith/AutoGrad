import React, { useState } from 'react';
import { Download } from 'lucide-react';
import api from '../api';
import { Button } from './ui';
import { useFeedback } from './feedback';

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
  const { toast, confirm } = useFeedback();

  const handleExport = async () => {
    if (!examId || disabled) return;
    const fallback = `AutoGrade_Results_${examId}.xlsx`;
    try {
      setDownloading(true);
      try {
        saveFile(await api.downloadExport(examId), fallback);
        toast.success('Final spreadsheet downloaded.');
      } catch (err) {
        if (err.response?.status !== 409) throw err;
        const detail = JSON.parse(await err.response.data.text()).detail;
        const wantsDraft = await confirm({
          title: 'Not all sheets are final',
          body: `${detail.message} Its file name, sheet title and a column all say so.`,
          confirmLabel: 'Download NOT FINAL copy',
        });
        if (wantsDraft) {
          saveFile(await api.downloadExport(examId, true), fallback);
          toast.info('Downloaded the copy marked NOT FINAL.');
        }
      }
    } catch (err) {
      console.error('Export download error:', err);
      toast.error('Could not download the spreadsheet. Check that this exam has results.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Button variant="primary" icon={Download} loading={downloading} disabled={disabled || !examId} onClick={handleExport}>
      Export
    </Button>
  );
}
