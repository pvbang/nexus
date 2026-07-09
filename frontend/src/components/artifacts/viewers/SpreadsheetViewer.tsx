// ============================================================
// MekongAI Nexus - SpreadsheetViewer
// Reads .xlsx/.xls via SheetJS and renders styled tables with sheet tabs.
// ============================================================
import { useState, useEffect, useCallback } from 'react';
import * as XLSX from 'xlsx';
import { AlertCircle, Loader2 } from 'lucide-react';
import { getDownloadUrl } from '../../../api/artifacts';
import { cn } from '../../../lib/cn';

interface SpreadsheetViewerProps {
  filePath: string;
  taskId?: string;
}

interface SheetData {
  name: string;
  rows: (string | number)[][];
}

export function SpreadsheetViewer({ filePath, taskId }: SpreadsheetViewerProps) {
  const [sheets, setSheets] = useState<SheetData[] | null>(null);
  const [active, setActive] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(getDownloadUrl(filePath, taskId));
      if (!res.ok) throw new Error(`Failed to fetch: ${res.statusText}`);
      const buf = await res.arrayBuffer();
      const wb = XLSX.read(buf, { type: 'array' });
      const parsed: SheetData[] = wb.SheetNames.map((name) => ({
        name,
        rows: XLSX.utils.sheet_to_json<(string | number)[]>(wb.Sheets[name], {
          header: 1,
          defval: '',
        }),
      }));
      setSheets(parsed);
      setActive(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read spreadsheet');
    } finally {
      setIsLoading(false);
    }
  }, [filePath, taskId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-red-600">
        <AlertCircle className="w-8 h-8" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }
  if (isLoading || !sheets) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
        <p className="text-sm text-gray-500">Reading spreadsheet...</p>
      </div>
    );
  }

  const current = sheets[active];
  const [head, ...body] = current.rows.length ? current.rows : [[]];

  return (
    <div className="flex flex-col h-full w-full">
      {sheets.length > 1 && (
        <div className="flex gap-1 px-3 py-2 border-b border-gray-200 bg-gray-50 overflow-x-auto shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setActive(i)}
              className={cn(
                'px-3 py-1 rounded-md text-xs font-medium whitespace-nowrap',
                i === active ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-200',
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-auto p-4">
        <table className="border-collapse text-sm">
          <thead>
            <tr>
              {head.map((cell, i) => (
                <th
                  key={i}
                  className="border border-gray-300 bg-blue-600 text-white font-semibold px-3 py-1.5 text-left whitespace-nowrap"
                >
                  {String(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri} className={ri % 2 ? 'bg-gray-50' : ''}>
                {head.map((_, ci) => (
                  <td
                    key={ci}
                    className="border border-gray-300 px-3 py-1.5 whitespace-nowrap"
                  >
                    {String(row[ci] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
