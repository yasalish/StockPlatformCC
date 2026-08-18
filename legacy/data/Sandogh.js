true == function() {
    // کف
    var min, ipos;
    var n1 = 10, n2 = 20, n3 = 30;

    function calculateKP(n) {
        min = [ih][0].PClosing;
        for (ipos = 0; ipos <= n; ipos++) {
            if (min > [ih][ipos].PClosing) {
                min = [ih][ipos].PClosing;
            }
        }
        return (((pc) - min) / min) * 100;
    }

    var kp1 = calculateKP(n1).toFixed(2);
    var kp2 = calculateKP(n2).toFixed(2);
    var kp3 = calculateKP(n3).toFixed(2);


    (cfield0) = kp1;
    (cfield1) = kp2;
    (cfield2) = kp3;

    if ((cs) == 68 && (l18)[(l18).length - 1] != '2') {
        return true;
    }
}();
