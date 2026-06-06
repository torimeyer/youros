/**
 * STAN Test Plan Parser
 * 
 * Extracts behavior rows from markdown tables for the test-plan executor.
 */

import * as fs from 'fs';
import * as path from 'path';

interface BehaviorRow {
  behavior: string;
  coverage: string;
  line: number;
  original: string;
}

export function parseStan(filePath: string): BehaviorRow[] {
  const content = fs.readFileSync(filePath, 'utf-8');
  const lines = content.split('\n');
  const rows: BehaviorRow[] = [];
  
  let inTable = false;
  let headers: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    
    if (line.startsWith('|') && line.endsWith('|')) {
      const parts = line.split('|').map(p => p.trim()).filter(p => p !== '');
      
      if (!inTable) {
        // Look for Behavior/Coverage headers
        const hasBehavior = parts.some(p => p.toLowerCase().includes('behavior'));
        const hasCoverage = parts.some(p => p.toLowerCase().includes('coverage'));
        
        if (hasBehavior || hasCoverage) {
          inTable = true;
          headers = parts;
          // Skip the separator row next
          i++;
          continue;
        }
      } else {
        // We are in a table row
        const rowData: Record<string, string> = {};
        headers.forEach((h, idx) => {
          rowData[h.toLowerCase()] = parts[idx] || '';
        });

        const behaviorKey = Object.keys(rowData).find(k => k.includes('behavior'));
        const coverageKey = Object.keys(rowData).find(k => k.includes('coverage') || k.includes('pre-deploy') || k.includes('post-deploy'));

        if (behaviorKey) {
          const behavior = rowData[behaviorKey];
          const coverage = coverageKey ? rowData[coverageKey] : 'Unknown';
          
          rows.push({
            behavior,
            coverage,
            line: i + 1,
            original: lines[i]
          });
        }
      }
    } else {
      inTable = false;
    }
  }

  return rows;
}

// CLI usage
if (require.main === module) {
  const target = process.argv[2];
  if (!target) {
    console.error('Usage: parse-stan <file.md>');
    process.exit(1);
  }
  
  try {
    const rows = parseStan(target);
    console.log(JSON.stringify(rows, null, 2));
  } catch (e) {
    console.error(`Error: ${e.message}`);
    process.exit(1);
  }
}
