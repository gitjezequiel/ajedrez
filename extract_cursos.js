const fs = require('fs');
const content = fs.readFileSync('index.html', 'utf8');
const start = content.indexOf('const CURSOS = [');
if (start === -1) { console.error('No se encontró const CURSOS'); process.exit(1); }

let depth = 0, i = start + 'const CURSOS = '.length, begin = i;
for (; i < content.length; i++) {
    if (content[i] === '[' || content[i] === '{') depth++;
    else if (content[i] === ']' || content[i] === '}') {
        depth--;
        if (depth === 0) { i++; break; }
    }
}

let CURSOS;
eval('CURSOS = ' + content.slice(begin, i));
fs.writeFileSync('cursos.json', JSON.stringify(CURSOS, null, 2));

const libros = CURSOS.find(c => c.id === 'libros');
const cursos = CURSOS.filter(c => c.id !== 'libros');

console.log('Total en array CURSOS:', CURSOS.length);
console.log('Cursos (tab Cursos):', cursos.length);
if (libros) {
    const totalSec = libros.secciones.length;
    const totalLec = libros.secciones.reduce((s, sec) => s + sec.lecciones.length, 0);
    console.log('Libros: 1 entrada con', totalSec, 'secciones y', totalLec, 'lecciones');
} else {
    console.log('Libros: no encontrado');
}
